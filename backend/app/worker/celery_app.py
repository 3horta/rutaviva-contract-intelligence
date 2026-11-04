"""Celery application (broker: Redis). Tasks live in `app/worker/tasks.py`.

Reliability settings, and why:
- `acks_late` + `task_reject_on_worker_lost`: a task is acknowledged only after it
  finishes, so if the worker dies mid-task the message is re-delivered (the document's
  lease lets the new delivery take it over).
- `worker_prefetch_multiplier=1`: a worker holds one long task at a time.
- No result backend: state lives in the `documents` table, not in Celery.

Logging and tracing (signal handlers at the bottom):
- `setup_logging`: connecting to it stops Celery from configuring the root logger, so the
  worker prints the same JSON lines as the API.
- `worker_process_init`: telemetry is configured in every prefork child, never in the
  parent process (the exporter's background thread would not survive the fork).
"""

from typing import Any

from celery import Celery
from celery.signals import setup_logging, worker_process_init

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.telemetry import configure_telemetry

TASK_PROCESS_DOCUMENT = "rutaviva.process_document"


def create_celery() -> Celery:
    settings = get_settings()
    app = Celery(
        "rutaviva",
        broker=settings.redis_url,
        include=["app.worker.tasks", "app.worker.diagnostics"],
    )
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


@setup_logging.connect
def _setup_logging(loglevel: str | int | None = None, **_: Any) -> None:
    # `loglevel` is the worker's `--loglevel` option.
    configure_logging(loglevel or get_settings().log_level)


@worker_process_init.connect
def _init_worker_process(**_: Any) -> None:
    settings = get_settings()
    configure_telemetry(settings.otel_service_name, settings)
    # Load the embedding model now, not in the first task (imported here: Celery imports
    # this module before the providers' settings are known).
    from app.providers import warm_up

    warm_up(settings)
