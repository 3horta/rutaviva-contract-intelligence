"""Celery application (broker: Redis). Tasks live in `app/worker/tasks.py`.

Reliability settings, and why:
- `acks_late` + `task_reject_on_worker_lost`: a task is acknowledged only after it
  finishes, so if the worker dies mid-task the message is re-delivered (the document's
  lease lets the new delivery take it over).
- `worker_prefetch_multiplier=1`: a worker holds one long task at a time.
- No result backend: state lives in the `documents` table, not in Celery.
"""

from celery import Celery

from app.core.config import get_settings

TASK_PROCESS_DOCUMENT = "rutaviva.process_document"


def create_celery() -> Celery:
    settings = get_settings()
    app = Celery("rutaviva", broker=settings.redis_url, include=["app.worker.tasks"])
    app.conf.update(
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_ignore_result=True,
        task_soft_time_limit=settings.task_soft_time_limit_s,
        task_time_limit=settings.task_time_limit_s,
        task_serializer="json",
        accept_content=["json"],
        broker_connection_retry_on_startup=True,
    )
    return app


celery_app = create_celery()
