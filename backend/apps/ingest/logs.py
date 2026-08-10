"""Turn a batch of log records into unsaved LogRecord rows.

One envelope item carries many records: an application emits logs constantly, and one HTTP
request per line would make logging the slowest thing it does.
"""

import uuid
from datetime import datetime
from typing import Any

from apps.ingest.normalize import _timestamp
from apps.logs.models import LogLevel, LogRecord
from apps.projects.models import Project

MAX_RECORDS = 500
MAX_BODY = 8192
MAX_ATTRIBUTES = 40


def logs_from_payload(
    project: Project, payload: dict[str, Any], *, now: datetime
) -> list[LogRecord]:
    raw = payload.get("items")
    if not isinstance(raw, list):
        return []

    # Envelope-level values, so a batch does not repeat them on every line.
    environment = _text(payload.get("environment"), 64)
    release = _text(payload.get("release"), 128)
    server_name = _text(payload.get("server_name"), 256)

    records = []
    for entry in raw[:MAX_RECORDS]:
        if not isinstance(entry, dict):
            continue

        body = entry.get("body")
        if body is None or body == "":
            # A log line with no message is a row nobody can read or search.
            continue

        records.append(
            LogRecord(
                id=uuid.uuid4(),
                project=project,
                timestamp=_timestamp(entry.get("timestamp"), now=now),
                level=_level(entry.get("level")),
                body=str(body)[:MAX_BODY],
                logger=_text(entry.get("logger"), 200),
                trace_id=_hex(entry.get("trace_id"), 32),
                span_id=_hex(entry.get("span_id"), 16),
                environment=environment,
                release=release,
                server_name=server_name,
                attributes=_attributes(entry.get("attributes")),
            )
        )
    return records


def _level(raw: Any) -> str:
    return raw if isinstance(raw, str) and raw in LogLevel.values else LogLevel.INFO


def _text(raw: Any, limit: int) -> str:
    if raw is None or isinstance(raw, dict | list):
        return ""
    return str(raw)[:limit]


def _hex(raw: Any, length: int) -> str:
    if not isinstance(raw, str):
        return ""
    value = raw.strip().lower()
    if len(value) != length or not all(c in "0123456789abcdef" for c in value):
        return ""
    return value


def _attributes(raw: Any) -> dict[str, Any]:
    """Flat and bounded. Attributes are filterable; a nested blob is not."""
    if not isinstance(raw, dict):
        return {}
    return {
        str(key)[:64]: value
        for key, value in list(raw.items())[:MAX_ATTRIBUTES]
        if not isinstance(value, dict | list)
    }
