"""Structured JSON logging with a request id on every line.

People share very personal things on this platform, so logs carry no PII
(spec 16.1): no email addresses, no codes, no IP addresses, no request bodies,
no query strings. Log ids and outcomes, never content. Use `redact` for
anything secret that has to appear at all.
"""

import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

# Set per request by RequestContextMiddleware so every log line can be tied back to
# the request that produced it, across the API and its background tasks.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# LogRecord attributes that are not caller-supplied `extra` fields, plus
# uvicorn's `color_message`, a copy of the message with terminal escapes.
_RESERVED = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()
    | {"message", "asctime", "taskName", "color_message"}
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id is not None:
            entry["request_id"] = request_id
        for key, value in record.__dict__.items():
            if key not in _RESERVED:
                entry[key] = value
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(level: str) -> None:
    """Route every logger through one JSON handler on stdout."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # Uvicorn's access log writes the client IP and the full path including
    # the query string. RequestLogMiddleware logs requests instead.
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
    # httpx logs full request URLs at INFO, which can carry credentials.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def redact(value: str | None, keep: int = 4) -> str:
    """Render a secret safe to log: ``abcd…(len=32)``."""
    if not value:
        return "<none>"
    return f"{value[:keep]}…(len={len(value)})"


logger = logging.getLogger("findmetalk")
