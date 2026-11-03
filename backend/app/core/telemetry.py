"""OpenTelemetry SDK setup: done once per process.

- API: `create_app` calls `configure_telemetry` and then `instrument_fastapi(app)`.
- Worker: `celery_app.py` calls `configure_telemetry` inside `worker_process_init`, that
  is, in every prefork child. It must not run in the parent: the span exporter uses a
  background thread, and threads do not survive `fork()`.

What gets traced automatically:
- FastAPI: one server span per request.
- SQLAlchemy: one span per query. Engines are created lazily (`session_factory_for`), so
  `create_engine` itself is instrumented and every engine created afterwards gets spans.
- Celery: the producer (the API) injects the W3C `traceparent` into the message headers
  and the worker continues the same trace, so an upload and its ingestion share one trace.

Without `OTEL_EXPORTER_OTLP_ENDPOINT` spans are still created (so logs carry a trace_id)
but nothing is exported. If the collector is down, the batch exporter drops the spans and
logs a warning in its own thread: requests and tasks are never blocked or failed by it.
"""

import logging

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.celery import CeleryInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

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

    # Without `engine=`, the instrumentor patches `create_engine`: covers lazy engines.
    SQLAlchemyInstrumentor().instrument()
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
    """One server span per request of this app.

    `/health` is traced too: it is the cheapest way to check the whole tracing pipeline
    (`curl /health`, then look for the trace in Jaeger).
    """
    FastAPIInstrumentor.instrument_app(app)
