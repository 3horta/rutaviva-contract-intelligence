"""Structured logging: one JSON object per line on stdout.

Every line carries `timestamp, level, logger, event, trace_id, span_id` plus every field
passed in `extra={...}`. `trace_id` is the same id Jaeger shows, so a log line can be
matched with its trace (and the other way around).

Usage in any module:

    logger = logging.getLogger(__name__)
    logger.info("document.ready", extra={"document_id": str(doc.id), "chunks": 12})

Rules: the message is a stable event name (`domain.action`); data goes in `extra`; never
log document content, prompts, answers or keys.

Each line logged while a span is recording is also added to that span as a span event,
so Jaeger shows it in the span's "Logs" section: the trace and its log lines in one view.
The event carries the same fields as the JSON line, minus the exception text (the span
already records exceptions).

The same setup runs in the API (called from `create_app`, after uvicorn has configured its
own loggers) and in the worker (through Celery's `setup_logging` signal, which stops Celery
from installing its own handlers).
"""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace
from opentelemetry.util.types import AttributeValue

# Attributes every LogRecord has. Anything else on a record came from `extra={...}`.
_STANDARD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}

# Loggers that ship their own handlers. They are emptied and made to propagate to the
# root logger, so their lines are JSON too and are not printed twice.
_THIRD_PARTY_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access", "celery")

# Not copied to spans: one access line per request repeats what the server span says.
_NOT_SPAN_EVENTS = ("uvicorn.access",)

# HTTP clients log every request URL at INFO. A provider may carry its key in the URL,
# and these lines would also become span events: keep them to warnings and errors.
_QUIET_LOGGERS = ("httpx", "httpcore")


def _extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    """The fields passed in `extra={...}`."""
    return {
        key: value
        for key, value in vars(record).items()
        if key not in _STANDARD_ATTRS and not key.startswith("_")
    }


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "trace_id": None,
            "span_id": None,
        }

        # The span that is current when the line is logged (same thread, same context).
        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            payload["trace_id"] = format(context.trace_id, "032x")
            payload["span_id"] = format(context.span_id, "016x")

        payload.update(_extra_fields(record))

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # default=str: UUIDs, datetimes and paths in `extra` are printed, never crash.
        return json.dumps(payload, default=str, ensure_ascii=False)


class _JsonHandler(logging.StreamHandler):
    """Marker class, so a second call can find and replace the handler it installed."""


def _attribute(value: Any) -> AttributeValue:
    """Span attributes take str, bool, int, float or lists of one of them; anything else
    (UUIDs, datetimes, dicts...) is stored as its string form."""
    if isinstance(value, str | bool | int | float):
        return value
    if isinstance(value, list | tuple) and all(isinstance(v, str) for v in value):
        return list(value)
    return str(value)


class SpanEventHandler(logging.Handler):
    """Adds each log line to the span that is current when it is logged, as a span event
    named after the event (`document.ready`, `llm.invalid_output`...)."""

    def emit(self, record: logging.LogRecord) -> None:
        if record.name in _NOT_SPAN_EVENTS:
            return
        span = trace.get_current_span()
        if not span.is_recording():
            return
        try:
            attributes: dict[str, AttributeValue] = {
                "log.level": record.levelname,
                "log.logger": record.name,
            }
            for key, value in _extra_fields(record).items():
                if value is not None:
                    attributes[key] = _attribute(value)
            if record.exc_info and record.exc_info[0] is not None:
                attributes["exception.type"] = record.exc_info[0].__name__
            span.add_event(record.getMessage(), attributes=attributes)
        except Exception:
            self.handleError(record)


def configure_logging(level: str | int = "INFO") -> None:
    """Send every log line to stdout as JSON. Safe to call more than once.

    Only the handlers installed here are replaced on a second call; other handlers on the
    root logger (e.g. pytest's log capture) are left alone.
    """
    if isinstance(level, str):
        level = level.upper()

    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, _JsonHandler | SpanEventHandler):
            root.removeHandler(handler)

    handler = _JsonHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.addHandler(SpanEventHandler())
    root.setLevel(level)

    for name in _THIRD_PARTY_LOGGERS:
        third_party = logging.getLogger(name)
        third_party.handlers.clear()
        third_party.propagate = True

    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
