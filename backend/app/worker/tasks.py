"""Celery tasks. Thin wrappers: the logic lives in `app/ingestion/pipeline.py`."""

import logging
import math
import uuid

from app.core import tracing
from app.core.config import get_settings
from app.core.errors import TransientProviderError
from app.db.models import FailureCode
from app.ingestion.pipeline import IngestionDeps, mark_failed, process_document
from app.worker.celery_app import TASK_PROCESS_DOCUMENT, celery_app

logger = logging.getLogger(__name__)


def build_deps() -> IngestionDeps:
    """Real dependencies, built on first use inside the worker process.

    Imports are local: Docling (heavy, Linux only) and the disk storage are only loaded
    when a task actually runs. Tests replace this function.
    """
    from app.db.session import session_factory_for
    from app.ingestion.extract import DoclingExtractor
    from app.ingestion.storage import LocalDiskStorage
    from app.providers import get_embeddings

    settings = get_settings()
    return IngestionDeps(
        session_factory=session_factory_for(settings.database_url),
        storage=LocalDiskStorage(settings.files_dir),
        extractor=DoclingExtractor(settings.docling_artifacts_path),
        embeddings=get_embeddings(settings),
        settings=settings,
    )


@celery_app.task(name=TASK_PROCESS_DOCUMENT, bind=True)
def process_document_task(self, document_id: str, tenant_id: str) -> str:
    """Ingest one document. Transient provider errors are retried with exponential
    backoff; when the retries run out the document ends `failed`
    (`embedding_unavailable`)."""
    deps = build_deps()
    settings = deps.settings
    retries = self.request.retries
    tracing.set_attributes(**{tracing.TASK_RETRIES: retries})

    document = uuid.UUID(document_id)
    tenant = uuid.UUID(tenant_id)
    try:
        return process_document(document, tenant, deps).status
    except TransientProviderError as exc:
        # The pipeline has already put the document back to `pending`.
        #
        # Retries exhausted is checked *before* calling `self.retry`: `retry` raises, so
        # code placed after it (or in a sibling `except`) would never run and the
        # document would stay `pending` forever.
        if retries >= settings.task_max_retries:
            detail = f"embedding provider unavailable after {retries + 1} attempts: {exc}"
            mark_failed(document, tenant, deps, FailureCode.EMBEDDING_UNAVAILABLE, detail)
            return "failed"

        countdown = settings.task_retry_backoff_s * 2**retries
        if exc.retry_after_s is not None:
            # The provider said when to come back (e.g. a 429 with Retry-After).
            countdown = max(countdown, math.ceil(exc.retry_after_s))
        logger.warning(
            "document.retry_scheduled",
            extra={
                "document_id": document_id,
                "tenant_id": tenant_id,
                "retry": retries + 1,
                "countdown_s": countdown,
            },
        )
        raise self.retry(
            exc=exc, countdown=countdown, max_retries=settings.task_max_retries
        ) from exc
