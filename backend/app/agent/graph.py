"""The agent as a LangGraph `StateGraph` (ADR-021).

    START -> decide --(tool calls)--------> tools --> decide
                    --(submit_answer)-----> submit --(valid / no attempts left)--> END
                                                   --(rejected, attempts left)---> decide
                    --(stop_reason / no call)----------------------------------> END

- `decide`: the policy (`policy.py`) takes one step: a model turn, or the deterministic
  workflow's next call.
- `tools`: LangGraph's `ToolNode` runs `search_contracts` / `read_passage`; their results
  extend the evidence ledger.
- `submit`: `submit_answer` is checked with the app's validator (`validate_answer`)
  against the passages in the ledger: JSON shape, the `LLMAnswer` schema, and every quote
  verbatim in a passage the agent read. A rejection goes back to the model as the tool's
  result, with the reason, while attempts remain (`1 + LLM_MAX_RETRIES`).

`run_agent` wraps the run in an `invoke_agent contract_analyst` span and turns the final
state into the same `AnswerOutcome` as the workflow (`generation.service.conclude`):
citations enriched from the ledger, confidence capped by the retrieval signal, the
injection and disagreement guardrails, every "not enough information" reason.
"""

import json
import time
import uuid
from typing import Any, Literal

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from sqlalchemy.orm import Session

from app.agent.policy import Policy, get_policy
from app.agent.state import AgentContext, AgentState, initial_state
from app.agent.tools import SUBMIT_ANSWER, read_passage, search_contracts
from app.core.config import Settings
from app.core.tracing import (
    AGENT_CONTRACT_ANALYST,
    AGENT_POLICY,
    AGENT_STEPS,
    AGENT_STOP_REASON,
    GEN_AI_AGENT_NAME,
    GEN_AI_CONVERSATION_ID,
    GEN_AI_OPERATION_NAME,
    OP_INVOKE_AGENT,
    TENANT_ID,
    set_attributes,
    span,
)
from app.generation.service import (
    AnswerOutcome,
    Generation,
    PhaseCallback,
    Retrieval,
    conclude,
    prepare_context,
)
from app.generation.validate import ValidatedAnswer, validate_answer
from app.providers.embeddings.base import EmbeddingsProvider
from app.providers.llm.base import ContextChunk, LLMProvider


def build_graph(policy: Policy) -> Any:
    graph = StateGraph(AgentState, context_schema=AgentContext)
    graph.add_node("decide", lambda state, runtime: policy.decide(state, runtime.context))
    graph.add_node("tools", ToolNode([search_contracts, read_passage]))
    graph.add_node("submit", lambda state, runtime: _submit(state, runtime.context, policy))
    graph.add_edge(START, "decide")
    graph.add_conditional_edges("decide", _after_decide, ["tools", "submit", "decide", END])
    graph.add_edge("tools", "decide")
    graph.add_conditional_edges("submit", _after_submit, ["decide", END])
    return graph.compile()


def run_agent(
    question: str,
    *,
    tenant_id: uuid.UUID,
    session: Session,
    embeddings: EmbeddingsProvider,
    llm: LLMProvider,
    settings: Settings,
    on_phase: PhaseCallback | None = None,
    conversation_id: uuid.UUID | None = None,
    policy: Policy | None = None,
) -> AnswerOutcome:
    """Answer with the agent. Same contract as `generation.service.answer_with_usage`."""
    started = time.perf_counter()
    policy = policy or get_policy(settings)
    ctx = AgentContext(
        question=question,
        tenant_id=tenant_id,
        session=session,
        embeddings=embeddings,
        llm=llm,
        settings=settings,
        notify=on_phase or (lambda _phase: None),
        conversation_id=conversation_id,
    )
    with span(
        f"{OP_INVOKE_AGENT} {AGENT_CONTRACT_ANALYST}",
        **{
            GEN_AI_OPERATION_NAME: OP_INVOKE_AGENT,
            GEN_AI_AGENT_NAME: AGENT_CONTRACT_ANALYST,
            GEN_AI_CONVERSATION_ID: str(conversation_id) if conversation_id else None,
            AGENT_POLICY: policy.name,
            TENANT_ID: str(tenant_id),
        },
    ) as agent_span:
        final: AgentState = build_graph(policy).invoke(
            initial_state(question),
            context=ctx,
            # Every model turn is at most three graph steps (decide, tools or submit).
            config={"recursion_limit": 3 * settings.agent_max_steps + 4},
        )
        stop = final["stop_reason"] or ("answered" if final["answer"] else "validation_failed")
        set_attributes(agent_span, **{AGENT_STEPS: final["steps"], AGENT_STOP_REASON: stop})

        evidence = final["evidence"]
        retrieval = Retrieval(evidence, len(evidence), judged=False)
        usable, warnings = prepare_context(agent_span, retrieval)
        generation = None
        if final["attempts"] or final["answer"] or stop in ("content_filtered", "budget"):
            generation = Generation(
                answer=final["answer"],
                attempts=final["attempts"],
                tokens_in=final["tokens_in"],
                tokens_out=final["tokens_out"],
                content_filtered=stop == "content_filtered",
            )
        return conclude(
            agent_span, tenant_id, retrieval, usable, warnings, generation, llm, started
        )


def _after_decide(state: AgentState) -> Literal["tools", "submit", "decide", "__end__"]:
    if state["stop_reason"]:
        return END
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        if any(call["name"] == SUBMIT_ANSWER for call in last.tool_calls):
            return "submit"
        return "tools"
    if isinstance(last, AIMessage):
        return END  # the deterministic policy had nothing the LLM may read
    return "decide"  # the model wrote text instead of a call: asked again


def _after_submit(state: AgentState) -> Literal["decide", "__end__"]:
    return END if state["answer"] is not None or state["stop_reason"] else "decide"


def _submit(state: AgentState, ctx: AgentContext, policy: Policy) -> dict[str, Any]:
    """Validate `submit_answer` against the evidence ledger."""
    last = state["messages"][-1]
    assert isinstance(last, AIMessage)
    call = next(c for c in last.tool_calls if c["name"] == SUBMIT_ANSWER)
    if policy.validates_submissions:
        # The deterministic policy's generation step already reported it.
        ctx.notify("validating")
    context = [
        ContextChunk(
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            filename=chunk.filename,
            page=chunk.page,
            text=chunk.content,
            flagged_injection=chunk.flagged_injection,
        )
        for chunk in state["evidence"]
        if not chunk.flagged_injection
    ]
    outcome = validate_answer(json.dumps(call["args"]), context)
    counted = 1 if policy.validates_submissions else 0
    if isinstance(outcome, ValidatedAnswer):
        return {"answer": outcome.answer, "attempts": counted, "stop_reason": "answered"}

    attempts = state["attempts"] + counted
    if attempts >= 1 + ctx.settings.llm_max_retries:
        return {"attempts": counted, "stop_reason": "validation_failed"}
    # Every call of the message gets a result, as the API requires; only the
    # submission carries the reason it was rejected.
    replies = [
        ToolMessage(
            content=(
                f"Rejected: {outcome.detail} Fix it and call submit_answer again."
                if c["id"] == call["id"]
                else "Not run: submit_answer was called in the same turn."
            ),
            tool_call_id=c["id"],
        )
        for c in last.tool_calls
    ]
    return {"attempts": counted, "messages": replies}
