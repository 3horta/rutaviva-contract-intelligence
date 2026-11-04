"""OpenTelemetry SDK setup: done once per process.

- API: `create_app` calls `configure_telemetry` and then `instrument_fastapi(app)`.
- Worker: `celery_app.py` calls `configure_telemetry` inside `worker_process_init`, that
  is, in every prefork child. It must not run in the parent: the span exporter uses a
  background thread, and threads do not survive `fork()`.

What gets traced automatically:
- FastAPI: one server span per request.
- SQLAlchemy: one span per query, from listeners registered on the `Engine` *class*, so
  they apply to every engine, including the ones `session_factory_for` creates lazily.
  (The packaged SQLAlchemy instrumentor is not used: it declares support only up to
  SQLAlchemy < 2.1, needs `greenlet` for its asyncio hook, and patches `create_engine`,
  which misses modules that imported it before telemetry was configured.)
- Celery: the producer (the API) injects the W3C `traceparent` into the message headers
  and the worker continues the same trace, so an upload and its ingestion share one trace.

Without `OTEL_EXPORTER_OTLP_ENDPOINT` spans are still created (so logs carry a trace_id)
but nothing is exported. If the collector is down, the batch exporter drops the spans and
logs a warning in its own thread: requests and tasks are never blocked or failed by it.
"""

import logging
from typing import Any

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.celery import CeleryInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode
from sqlalchemy import Engine, event

from app.core.config import Settings

logger = logging.getLogger(__name__)

# Seconds the exporter waits for the collector before giving up on a batch.
_EXPORT_TIMEOUT_S = 5

_configured = False


def configure_telemetry(service_name: str, settings: Settings) -> None:
    """Install the global TracerProvider and the SQLAlchemy and Celery instrumentation.

    Idempotent: later calls in the same process do nothing. If a TracerProvider was
    already installed (for example by a test that captures spans), it is kept.
    """
    global _configured
    if _configured:
        return

    if isinstance(trace.get_tracer_provider(), trace.ProxyTracerProvider):
        provider = TracerProvider(resource=Resource.create({SERVICE_NAME: service_name}))
        if settings.otel_exporter_otlp_endpoint:
            exporter = OTLPSpanExporter(
                endpoint=settings.otel_exporter_otlp_endpoint,
                insecure=True,  # plain gRPC inside the Compose network
                timeout=_EXPORT_TIMEOUT_S,
            )
            provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

    _trace_sql_queries()
    # Producer side in the API (injects the trace context); consumer side in the worker.
    CeleryInstrumentor().instrument()

    _configured = True
    logger.info(
        "telemetry.configured",
        extra={
            "service_name": service_name,
            "exporting": bool(settings.otel_exporter_otlp_endpoint),
        },
    )


def instrument_fastapi(app: FastAPI) -> None:
    """One server span per request of this app, except `/health`: the container
    healthcheck calls it every few seconds, and those traces would bury the real ones.
    (To check the pipeline, any other request works, e.g. `GET /documents`.)"""
    FastAPIInstrumentor.instrument_app(app, excluded_urls="/health")


# --- SQL query spans ---------------------------------------------------------------------

_db_tracer = trace.get_tracer("rutaviva.db")


def _trace_sql_queries() -> None:
    """One CLIENT span per SQL statement, child of whatever span is current.

    The span carries the SQL text with its placeholders, never the bound parameters
    (they can hold document content).
    """
    event.listen(Engine, "before_cursor_execute", _start_query_span)
    event.listen(Engine, "after_cursor_execute", _end_query_span)
    event.listen(Engine, "handle_error", _fail_query_span)


def _start_query_span(conn: Any, cursor: Any, statement: str, *_: Any) -> None:
    # "SELECT", "INSERT"...: a short span name; the full statement goes in an attribute.
    operation = statement.split(maxsplit=1)[0].upper() if statement.strip() else "SQL"
    span = _db_tracer.start_span(
        operation,
        kind=SpanKind.CLIENT,
        attributes={
            "db.system": conn.engine.dialect.name,
            "db.name": conn.engine.url.database or "",
            "db.statement": statement,
        },
    )
    # The connection's `info` dict lives as long as the DBAPI connection: a safe place to
    # hand the span over to the "after" listener of the same statement.
    conn.info["otel_query_span"] = span


def _end_query_span(conn: Any, *_: Any) -> None:
    span = conn.info.pop("otel_query_span", None)
    if span is not None:
        span.end()


def _fail_query_span(context: Any) -> None:
    connection = context.connection
    span = connection.info.pop("otel_query_span", None) if connection is not None else None
    if span is not None:
        span.set_status(Status(StatusCode.ERROR, type(context.original_exception).__name__))
        span.end()
