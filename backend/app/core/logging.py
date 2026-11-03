"""Structured logging: one JSON object per line on stdout.

Every line carries `timestamp, level, logger, event, trace_id, span_id` plus every field
passed in `extra={...}`. `trace_id` is the same id Jaeger shows, so a log line can be
matched with its trace (and the other way around).

Usage in any module:

    logger = logging.getLogger(__name__)
    logger.info("document.ready", extra={"document_id": str(doc.id), "chunks": 12})

Rules: the message is a stable event name (`domain.action`); data goes in `extra`; never
log document content, prompts, answers or keys.

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

# Attributes every LogRecord has. Anything else on a record came from `extra={...}`.
_STANDARD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}

# Loggers that ship their own handlers. They are emptied and made to propagate to the
# root logger, so their lines are JSON too and are not printed twice.
_THIRD_PARTY_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access", "celery")


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

        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # default=str: UUIDs, datetimes and paths in `extra` are printed, never crash.
        return json.dumps(payload, default=str, ensure_ascii=False)


class _JsonHandler(logging.StreamHandler):
    """Marker class, so a second call can find and replace the handler it installed."""


def configure_logging(level: str | int = "INFO") -> None:
    """Send every log line to stdout as JSON. Safe to call more than once.

    Only the handler installed here is replaced on a second call; other handlers on the
    root logger (e.g. pytest's log capture) are left alone.
    """
    if isinstance(level, str):
        level = level.upper()

    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, _JsonHandler):
            root.removeHandler(handler)

    handler = _JsonHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)

    for name in _THIRD_PARTY_LOGGERS:
        third_party = logging.getLogger(name)
        third_party.handlers.clear()
        third_party.propagate = True
