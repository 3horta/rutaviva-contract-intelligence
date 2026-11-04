"""Log lines copied to the current span as span events, so Jaeger shows them."""

import logging
import uuid

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.core.logging import SpanEventHandler


@pytest.fixture
def exporter() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.fixture
def tracer(exporter: InMemorySpanExporter):
    # A local provider: the global one may already be installed by another test.
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("test")


@pytest.fixture
def logger():
    log = logging.getLogger("rutaviva.test_span_events")
    log.setLevel(logging.DEBUG)
    # Not to the root logger: a test that built the app left a SpanEventHandler there.
    log.propagate = False
    handler = SpanEventHandler()
    log.addHandler(handler)
    yield log
    log.removeHandler(handler)
    log.propagate = True


def test_a_log_line_becomes_an_event_of_the_current_span(tracer, logger, exporter) -> None:
    document_id = uuid.uuid4()
    with tracer.start_as_current_span("ingest"):
        logger.warning(
            "document.failed", extra={"document_id": document_id, "chunks": 3, "missing": None}
        )

    [span] = exporter.get_finished_spans()
    [event] = span.events
    assert event.name == "document.failed"
    assert event.attributes["log.level"] == "WARNING"
    assert event.attributes["log.logger"] == "rutaviva.test_span_events"
    assert event.attributes["document_id"] == str(document_id)  # non-primitive -> str
    assert event.attributes["chunks"] == 3
    assert "missing" not in event.attributes  # None is not a valid attribute value


def test_an_exception_adds_its_type_but_not_its_text(tracer, logger, exporter) -> None:
    with tracer.start_as_current_span("ingest"):
        try:
            raise ValueError("secret detail")
        except ValueError:
            logger.exception("document.crashed")

    [event] = exporter.get_finished_spans()[0].events
    assert event.attributes["exception.type"] == "ValueError"
    assert all("secret detail" not in str(v) for v in event.attributes.values())


def test_outside_a_span_nothing_happens(logger) -> None:
    logger.info("app.started")  # no current span: must not raise


def test_access_log_lines_are_not_copied(tracer, exporter) -> None:
    access = logging.getLogger("uvicorn.access")
    handler = SpanEventHandler()
    access.addHandler(handler)
    try:
        with tracer.start_as_current_span("GET /documents"):
            access.warning("GET /documents 200")
    finally:
        access.removeHandler(handler)

    assert exporter.get_finished_spans()[0].events == ()
