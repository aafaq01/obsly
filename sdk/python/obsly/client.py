"""The client and the module-level API."""

import logging
import os
import socket
import uuid
from datetime import UTC, datetime
from types import TracebackType
from typing import Any

from obsly import dsn as dsn_module
from obsly.stacktrace import exception_chain
from obsly.transport import Transport, build_envelope

logger = logging.getLogger("obsly")

SDK = {"name": "obsly.python", "version": "0.1.0"}


class Client:
    def __init__(
        self,
        dsn: str,
        *,
        environment: str = "production",
        release: str = "",
        server_name: str | None = None,
        send_default_pii: bool = False,
        tags: dict[str, str] | None = None,
        transport: Transport | None = None,
    ) -> None:
        parsed = dsn_module.parse(dsn)
        self.environment = environment
        self.release = release
        self.server_name = server_name if server_name is not None else socket.gethostname()
        # Off by default. Turning on the capture of user identifiers, addresses and request
        # bodies must be a decision somebody made, not a default they inherited.
        self.send_default_pii = send_default_pii
        self.tags = dict(tags or {})
        self._transport = transport or Transport(parsed.envelope_url, parsed.public_key)

    def capture_exception(self, exc: BaseException, *, extra: dict[str, Any] | None = None) -> str:
        return self._capture({"exception": {"values": exception_chain(exc)}}, extra)

    def capture_message(
        self, message: str, *, level: str = "info", extra: dict[str, Any] | None = None
    ) -> str:
        return self._capture({"message": message, "level": level}, extra)

    def _capture(self, payload: dict[str, Any], extra: dict[str, Any] | None) -> str:
        event_id = str(uuid.uuid4())

        event: dict[str, Any] = {
            "event_id": event_id,
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "platform": "python",
            "level": payload.pop("level", "error"),
            "environment": self.environment,
            "release": self.release,
            "server_name": self.server_name,
            "sdk": SDK,
            "tags": {**self.tags, **(extra or {}).pop("tags", {})} if extra else dict(self.tags),
            **payload,
        }
        if extra:
            event["extra"] = extra

        envelope = build_envelope(
            {"event_id": event_id, "sent_at": event["timestamp"]},
            [("event", event)],
        )
        self._transport.send(envelope)
        return event_id

    def flush(self, timeout: float = 2.0) -> bool:
        return self._transport.flush(timeout)

    def close(self) -> None:
        self._transport.close()


_client: Client | None = None


def init(dsn: str | None = None, **options: Any) -> Client | None:
    """Configure the SDK.

    A missing or unparseable DSN disables reporting instead of raising. Nobody wants their
    service to refuse to boot because the telemetry endpoint was misconfigured, and DSN is read
    from the environment so that a deploy can turn reporting off without a code change.
    """
    global _client

    dsn = dsn or os.environ.get("OBSLY_DSN", "")
    if not dsn:
        logger.info("obsly: no DSN configured, error reporting is disabled")
        _client = None
        return None

    try:
        _client = Client(dsn, **options)
    except dsn_module.DsnError as exc:
        logger.warning("obsly: disabled, %s", exc)
        _client = None

    return _client


def get_client() -> Client | None:
    return _client


def capture_exception(exc: BaseException | None = None, **kwargs: Any) -> str | None:
    if _client is None:
        return None
    if exc is None:
        import sys

        exc = sys.exc_info()[1]
    if exc is None:
        return None
    return _client.capture_exception(exc, **kwargs)


def capture_message(message: str, **kwargs: Any) -> str | None:
    return None if _client is None else _client.capture_message(message, **kwargs)


def flush(timeout: float = 2.0) -> bool:
    return True if _client is None else _client.flush(timeout)


__all__ = [
    "Client",
    "TracebackType",
    "capture_exception",
    "capture_message",
    "flush",
    "get_client",
    "init",
]
