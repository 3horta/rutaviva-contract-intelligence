from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.config import EmbeddingsProvider, LLMProvider, Settings, get_settings

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    llm_provider: LLMProvider
    llm_model: str | None
    embeddings_provider: EmbeddingsProvider
    embedding_model: str


@router.get("/health", response_model=HealthResponse)
def health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    return HealthResponse(
        status="ok",
        llm_provider=settings.llm_provider,
        llm_model=settings.llm_model if settings.llm_provider != "fake" else None,
        embeddings_provider=settings.embeddings_provider,
        embedding_model=settings.embedding_model_id,
    )
