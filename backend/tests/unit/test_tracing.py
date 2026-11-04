"""Span helpers: links from a query to the ingestion trace of its sources."""

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.core.tracing import current_span_id, current_trace_id, link_to

INGEST_TRACE = "0af7651916cd43dd8448eb211c80319c"
INGEST_SPAN = "b7ad6b7169203331"


def _provider() -> tuple[TracerProvider, InMemorySpanExporter]:
    # A local provider: the global one may already be installed by another test.
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def test_link_points_to_the_ingestion_span() -> None:
    provider, exporter = _provider()
    with provider.get_tracer("test").start_as_current_span("query.answer"):
        link_to(INGEST_TRACE, INGEST_SPAN, **{"document.id": "doc-1"})

    [span] = exporter.get_finished_spans()
    [link] = span.links
    assert format(link.context.trace_id, "032x") == INGEST_TRACE
    assert format(link.context.span_id, "016x") == INGEST_SPAN
    assert link.attributes["document.id"] == "doc-1"


def test_missing_or_invalid_ids_add_no_link() -> None:
    provider, exporter = _provider()
    with provider.get_tracer("test").start_as_current_span("query.answer"):
        link_to(None, INGEST_SPAN)
        link_to(INGEST_TRACE, None)
        link_to("not-hex", "zz")
        link_to("0" * 32, "0" * 16)  # all-zero ids are invalid in OpenTelemetry

    [span] = exporter.get_finished_spans()
    assert span.links == ()


def test_current_ids_inside_and_outside_a_span() -> None:
    provider, _ = _provider()
    assert current_trace_id() is None and current_span_id() is None
    with provider.get_tracer("test").start_as_current_span("x"):
        assert len(current_trace_id()) == 32
        assert len(current_span_id()) == 16
