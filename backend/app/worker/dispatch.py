"""`TaskDispatcher` backed by Celery: what the API uses to enqueue an ingestion.

`send_task` sends the message by task *name*, so the API never imports the task module
(and with it the PDF extraction stack, which is only installed in the worker image).
The trace context travels in the message headers (Celery instrumentation), so the
worker's spans join the trace of the upload request.
"""

import logging
import uuid

from app.worker.celery_app import TASK_PROCESS_DOCUMENT, celery_app

logger = logging.getLogger(__name__)


class CeleryDispatcher:
    def enqueue_document(self, document_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
        # Plain strings: the messages are JSON.
        celery_app.send_task(TASK_PROCESS_DOCUMENT, args=[str(document_id), str(tenant_id)])
        logger.info(
            "document.enqueued",
            extra={"document_id": str(document_id), "tenant_id": str(tenant_id)},
        )
