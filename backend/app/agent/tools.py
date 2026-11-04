"""The agent's tools: function-calling definitions over the app's own operations.

- `search_contracts(query)`: the same embedding + hybrid search + reranker as the
  workflow (`generation.service.retrieve`), for any query the model writes.
- `read_passage(chunk_id, neighbours)`: a passage and its neighbours in the same
  document (`mcp.tools.get_passage`, the same function the MCP server exposes).
- `submit_answer(...)`: not executed; the model calls it to finish. Its arguments are
  the `LLMAnswer` schema, validated by the graph against the evidence ledger.

Every chunk a tool returns is added to the state's `evidence` (a `Command` update), and
the model gets the passages as delimited data. Passages flagged as prompt injection are
listed with their text withheld (ADR-013). The tenant, session and providers come from
the run's `AgentContext` through `ToolRuntime`, which is not part of the schema the
model sees. Each call is a GenAI `execute_tool {name}` span (ADR-018) with ids only.
"""

import uuid
from typing import Annotated, Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command
from pydantic import Field

from app.agent.state import AgentContext
from app.core.errors import NotFound
from app.core.tracing import (
    GEN_AI_OPERATION_NAME,
    GEN_AI_TOOL_CALL_ID,
    GEN_AI_TOOL_NAME,
    GEN_AI_TOOL_TYPE,
    OP_EXECUTE_TOOL,
    span,
)
from app.generation.prompt import escape_document_text
from app.generation.service import retrieve
from app.mcp import tools as shared_tools
from app.mcp.schemas import WITHHELD_TEXT
from app.retrieval.hybrid import RetrievedChunk
from app.schemas.query import LLMAnswer

SEARCH_CONTRACTS = "search_contracts"
READ_PASSAGE = "read_passage"
SUBMIT_ANSWER = "submit_answer"

# `Any`, not `AgentContext`: pydantic would try to build a validator for the context,
# whose fields are protocols. `runtime.context` is the run's `AgentContext`.
AgentRuntime = ToolRuntime[Any, Any]


@tool(SEARCH_CONTRACTS)
def search_contracts(
    query: Annotated[
        str,
        Field(min_length=1, max_length=500, description="What to look for in the contracts."),
    ],
    runtime: AgentRuntime,
) -> Command:
    """Hybrid search (semantic + full text, reranked) over the organisation's contracts.
    Returns the best passages with their id, file and page. Search again with other
    words if the passages do not answer the question."""
    ctx = runtime.context
    with _tool_span(SEARCH_CONTRACTS, runtime.tool_call_id):
        ctx.notify("searching")
        chunks = retrieve(
            query,
            tenant_id=ctx.tenant_id,
            session=ctx.session,
            embeddings=ctx.embeddings,
            settings=ctx.settings,
        )
    text = render_passages(chunks) if chunks else "No passages found for this query."
    return _result(runtime.tool_call_id, text, chunks)


@tool(READ_PASSAGE)
def read_passage(
    chunk_id: Annotated[str, Field(description="The id of a passage from a search result.")],
    runtime: AgentRuntime,
    neighbours: Annotated[
        int, Field(ge=0, le=shared_tools.MAX_NEIGHBOURS, description="Passages on each side.")
    ] = 1,
) -> Command:
    """Read a passage together with the passages around it in the same document, to see
    a clause that continues or the context of a definition."""
    ctx = runtime.context
    with _tool_span(READ_PASSAGE, runtime.tool_call_id):
        try:
            result = shared_tools.get_passage(
                ctx.session, ctx.tenant_id, chunk_id=uuid.UUID(chunk_id), neighbours=neighbours
            )
        except (ValueError, NotFound):
            return _result(runtime.tool_call_id, "No passage with that id.", [])
    chunks = [
        RetrievedChunk(
            chunk_id=p.chunk_id,
            document_id=result.document_id,
            filename=result.filename,
            page=p.page,
            # The real text: the ledger needs it to check quotes; the model gets the
            # withheld marker for flagged passages (render_passages).
            content=_stored_text(ctx, p.chunk_id) if p.flagged_injection else p.text,
            flagged_injection=p.flagged_injection,
            score=0.0,
            vec_rank=None,
            fts_rank=None,
        )
        for p in result.passages
    ]
    return _result(runtime.tool_call_id, render_passages(chunks), chunks)


def submit_answer_tool() -> dict[str, Any]:
    """`submit_answer` as an OpenAI-format tool: the `LLMAnswer` schema (references
    inlined, which Gemini needs)."""
    definition = convert_to_openai_tool(LLMAnswer)
    definition["function"]["name"] = SUBMIT_ANSWER
    definition["function"]["description"] = (
        "Finish with the answer. Every statement needs a citation: the id of a passage you "
        "read and a quote copied VERBATIM from it. If the passages do not contain the "
        "answer, set insufficient_evidence to true and leave citations empty."
    )
    return definition


def render_passages(chunks: list[RetrievedChunk]) -> str:
    """Passages as delimited data: `<passage>` tags whose content cannot close them."""
    blocks = []
    for chunk in chunks:
        text = WITHHELD_TEXT if chunk.flagged_injection else escape_passage(chunk.content)
        blocks.append(
            f'<passage id="{chunk.chunk_id}" file="{escape_passage(chunk.filename)}" '
            f'page="{chunk.page}">\n{text}\n</passage>'
        )
    return "\n\n".join(blocks)


def escape_passage(text: str) -> str:
    return (
        escape_document_text(text)
        .replace("<passage", "&lt;passage")
        .replace("</passage", "&lt;/passage")
    )


def _result(tool_call_id: str, text: str, chunks: list[RetrievedChunk]) -> Command:
    return Command(
        update={
            "evidence": chunks,
            "messages": [ToolMessage(content=text, tool_call_id=tool_call_id)],
        }
    )


def _stored_text(ctx: AgentContext, chunk_id: uuid.UUID) -> str:
    from sqlalchemy import select

    from app.db.models import Chunk

    content = ctx.session.scalar(
        select(Chunk.content).where(Chunk.id == chunk_id, Chunk.tenant_id == ctx.tenant_id)
    )
    return content or ""


def _tool_span(name: str, call_id: str | None):  # noqa: ANN202 - a span context manager
    return span(
        f"{OP_EXECUTE_TOOL} {name}",
        **{
            GEN_AI_OPERATION_NAME: OP_EXECUTE_TOOL,
            GEN_AI_TOOL_NAME: name,
            GEN_AI_TOOL_TYPE: "function",
            GEN_AI_TOOL_CALL_ID: call_id,
        },
    )
