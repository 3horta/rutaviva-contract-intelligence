from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.config import EmbeddingsProvider, LLMProvider, Settings, get_settings
from app.providers.llm.router import model_for

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    llm_provider: LLMProvider
    # The model that drafts answers and the one for the small jobs (follow-ups, relevance
    # judge); null in keyless mode. Never any key.
    llm_model: str | None
    llm_fast_model: str | None
    embeddings_provider: EmbeddingsProvider
    embedding_model: str


@router.get("/health", response_model=HealthResponse)
def health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    real_llm = settings.llm_provider != "fake"
    return HealthResponse(
        status="ok",
        llm_provider=settings.llm_provider,
        llm_model=model_for(settings, "answer") if real_llm else None,
        llm_fast_model=model_for(settings, "judge") if real_llm else None,
        embeddings_provider=settings.embeddings_provider,
        embedding_model=settings.embedding_model_id,
    )
