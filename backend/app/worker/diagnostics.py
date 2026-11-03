"""Diagnostic task to check that a trace crosses from the API process to the worker.

From inside the api container:

    docker compose exec api python -m app.worker.diagnostics

It starts a span, sends `rutaviva.diagnostics.ping` by name and prints the trace id.
The worker's span for the task shows up in Jaeger under that same trace id. The task
touches no data and needs no tenant.
"""

import logging

from opentelemetry import trace

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.telemetry import configure_telemetry
from app.core.tracing import current_trace_id, span
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

TASK_DIAGNOSTICS_PING = "rutaviva.diagnostics.ping"


@celery_app.task(name=TASK_DIAGNOSTICS_PING)
def ping() -> str:
    with span("diagnostics.ping"):
        logger.info("diagnostics.ping")
    return "pong"


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    configure_telemetry(settings.otel_service_name, settings)

    with span("diagnostics.send_ping"):
        trace_id = current_trace_id()
        celery_app.send_task(TASK_DIAGNOSTICS_PING)
        logger.info("diagnostics.ping_sent", extra={"task": TASK_DIAGNOSTICS_PING})

    # This process exits right away: export the buffered spans before it does.
    trace.get_tracer_provider().force_flush()
    print(trace_id)


if __name__ == "__main__":
    main()
