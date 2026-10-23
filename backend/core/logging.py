"""Basic structured (JSON lines) logging setup."""

import json
import logging
from datetime import datetime, timezone

from backend.core.redact import install_log_redaction, redact

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message"}


class JsonFormatter(logging.Formatter):
    """Format log records as one JSON object per line, including `extra=` fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        payload.update({k: v for k, v in record.__dict__.items() if k not in _RESERVED})
        if record.exc_info or record.exc_text:
            payload["exc"] = record.exc_text or self.formatException(record.exc_info)
        return redact(json.dumps(payload, default=str))  # also covers extra= fields


def setup_logging(level: str = "INFO") -> None:
    """Configure the root logger to emit JSON lines to stderr, with API keys redacted."""
    install_log_redaction()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
