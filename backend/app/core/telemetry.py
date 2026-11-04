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

Metrics (ADR-020): a MeterProvider with a periodic OTLP exporter to the same collector,
which exposes them for Prometheus on :8889. The instruments live in `app/core/metrics.py`.
Every process gets its own `service.instance.id`, so the series of two worker processes
(or of a restarted API) never overwrite each other in the collector.

Without `OTEL_EXPORTER_OTLP_ENDPOINT` spans are still created (so logs carry a trace_id)
but nothing is exported, and no metrics are recorded. If the collector is down, the
exporters drop the data and log a warning in their own thread: requests and tasks are
never blocked or failed by it.
"""

import logging
import uuid
from typing import Any

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.celery import CeleryInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_INSTANCE_ID, SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode
from sqlalchemy import Engine, event

from app.core.config import Settings

logger = logging.getLogger(__name__)

# Seconds the exporter waits for the collector before giving up on a batch.
_EXPORT_TIMEOUT_S = 5
# How often metrics are pushed to the collector.
_METRICS_EXPORT_INTERVAL_MS = 15_000

_configured = False


def configure_telemetry(service_name: str, settings: Settings) -> None:
    """Install the global TracerProvider (and MeterProvider, when exporting) and the
    SQLAlchemy and Celery instrumentation.

    Idempotent: later calls in the same process do nothing. If a TracerProvider was
    already installed (for example by a test that captures spans), it is kept.
    """
    global _configured
    if _configured:
        return

    resource = Resource.create({SERVICE_NAME: service_name, SERVICE_INSTANCE_ID: str(uuid.uuid4())})
    if isinstance(trace.get_tracer_provider(), trace.ProxyTracerProvider):
        provider = TracerProvider(resource=resource)
        if settings.otel_exporter_otlp_endpoint:
            exporter = OTLPSpanExporter(
                endpoint=settings.otel_exporter_otlp_endpoint,
                insecure=True,  # plain gRPC inside the Compose network
                timeout=_EXPORT_TIMEOUT_S,
            )
            provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
    if settings.otel_exporter_otlp_endpoint:
        _configure_metrics(resource, settings.otel_exporter_otlp_endpoint)

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


def _configure_metrics(resource: Resource, endpoint: str) -> None:
    """Install the global MeterProvider. The instruments in `app/core/metrics.py` were
    created from the global proxy meter, which forwards to this provider from now on."""
    exporter = OTLPMetricExporter(endpoint=endpoint, insecure=True, timeout=_EXPORT_TIMEOUT_S)
    reader = PeriodicExportingMetricReader(
        exporter, export_interval_millis=_METRICS_EXPORT_INTERVAL_MS
    )
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))


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
