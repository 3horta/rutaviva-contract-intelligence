"""Question answering endpoint over the tenant's documents.

Plain JSON for now; SSE streaming comes in a later milestone. `conversation_id` is
echoed as null until conversations are stored.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.core.config import Settings, get_settings
from app.generation.service import answer_question
from app.providers import get_embeddings
from app.providers.llm import get_llm
from app.schemas.query import QueryRequest, QueryResponse

router = APIRouter(tags=["query"])


# A sync handler on purpose: retrieval and the providers are blocking calls, and FastAPI
# runs sync handlers in its thread pool so they do not block the event loop.
@router.post("/query", response_model=QueryResponse)
def query(
    body: QueryRequest,
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
    session: Annotated[Session, Depends(get_tenant_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> QueryResponse:
    # The tenant comes only from the verified token (get_current_tenant), never from
    # the request body; the session already has it set for RLS.
    return answer_question(
        body.question,
        tenant_id=tenant.id,
        session=session,
        embeddings=get_embeddings(settings),
        llm=get_llm(settings),
        settings=settings,
    )
