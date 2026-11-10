"""Small JSON log formatter shared by the ingest API and worker."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

_EXTRA_FIELDS = (
    "request_id",
    "token_label",
    "source_ip",
    "job_id",
    "worker_id",
    "sha256",
    "source_filename",
    "status",
    "duration_ms",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name in _EXTRA_FIELDS:
            value = getattr(record, name, None)
            if value is not None:
                payload[name] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure(level: str = "INFO") -> None:
    """Configure the root logger once for container output."""

    root = logging.getLogger()
    root.setLevel(level.upper())
    if any(isinstance(handler.formatter, JsonFormatter) for handler in root.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.handlers = [handler]
