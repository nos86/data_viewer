"""Structured logs with a request id (RI-44).

Every record carries `request_id` and `user` (contextvars set by the request middleware, or inherited by the ingestion process that
a request started), so one request can be followed across the API, the conversion and the background work. Records can be JSON lines
(for Databricks / log collectors) or plain text (local use). Tokens and other credentials are never logged.
"""
from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
import uuid
from typing import Any

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
user_var: contextvars.ContextVar[str] = contextvars.ContextVar("user", default="-")

_STANDARD = set(logging.LogRecord("x", 0, "x", 0, "", (), None).__dict__) | {"message", "asctime", "request_id", "user"}
_factory_installed = False


def _install_factory() -> None:
    global _factory_installed
    if _factory_installed:
        return
    old = logging.getLogRecordFactory()

    def factory(*a: Any, **kw: Any) -> logging.LogRecord:
        r = old(*a, **kw)
        r.request_id = request_id_var.get()
        r.user = user_var.get()
        return r

    logging.setLogRecordFactory(factory)
    _factory_installed = True


def extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    return {k: v for k, v in record.__dict__.items() if k not in _STANDARD and not k.startswith("_")}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        doc: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "user": getattr(record, "user", "-"),
            **extra_fields(record),
        }
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)
        return json.dumps(doc, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extra = " ".join(f"{k}={v}" for k, v in extra_fields(record).items())
        base = f"{self.formatTime(record, '%H:%M:%S')} {record.levelname:<7} [{getattr(record, 'request_id', '-')}] {record.name}: {record.getMessage()}"
        out = f"{base} {extra}".rstrip()
        return out + ("\n" + self.formatException(record.exc_info) if record.exc_info else "")


def configure_logging(level: str = "INFO", fmt: str = "text") -> None:
    """Idempotent: replaces the handler of the `viewer` logger tree."""
    _install_factory()
    root = logging.getLogger("viewer")
    root.setLevel(level.upper())
    for h in list(root.handlers):
        if getattr(h, "_viewer", False):
            root.removeHandler(h)
    h = logging.StreamHandler(sys.stderr)
    h._viewer = True  # type: ignore[attr-defined]
    h.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    root.addHandler(h)


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def bind(request_id: str | None = None, user: str | None = None) -> None:
    if request_id:
        request_id_var.set(request_id)
    if user:
        user_var.set(user)
