"""Tests for the JSON log formatter.

``logging`` reserves attribute names such as ``filename`` and ``module``. Passing
one of them through ``extra=`` raises ``KeyError`` while logging, so the field
list is checked here as well as in the API upload test.
"""

from __future__ import annotations

import json
import logging

from transducer_ingest.service.log import _EXTRA_FIELDS, JsonFormatter

RESERVED = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "message",
    "asctime",
}


def test_extra_fields_do_not_shadow_log_record_attributes() -> None:
    assert RESERVED.isdisjoint(_EXTRA_FIELDS)


def test_formatter_emits_structured_fields() -> None:
    record = logging.LogRecord(
        name="transducer.ingest.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="upload accepted",
        args=(),
        exc_info=None,
    )
    record.request_id = "abc"
    record.token_label = "workstation"
    record.source_filename = "logger.xle"
    record.status = "queued"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["message"] == "upload accepted"
    assert payload["request_id"] == "abc"
    assert payload["token_label"] == "workstation"
    assert payload["source_filename"] == "logger.xle"
    assert payload["status"] == "queued"
    # The built-in record filename must not leak into the payload.
    assert "filename" not in payload
