"""Background delivery.

Two rules govern everything here:

1. Reporting an error must never make the host application slower. Capture puts an envelope on
   a queue and returns; a worker thread does the network call.
2. Reporting an error must never raise. An SDK that crashes the process it is observing is
   worse than no SDK, so every failure path here ends in a log line and a dropped event.
"""

import atexit
import contextlib
import json
import logging
import queue
import threading
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger("obsly")

CONTENT_TYPE = "application/x-obsly-envelope"


def build_envelope(headers: dict[str, Any], items: list[tuple[str, dict[str, Any]]]) -> bytes:
    """Serialise to the NDJSON wire format, with byte lengths so payloads may contain newlines."""
    lines = [_dumps(headers)]
    for item_type, payload in items:
        body = _dumps(payload)
        lines.append(_dumps({"type": item_type, "length": len(body)}))
        lines.append(body)
    return b"\n".join(lines) + b"\n"


def _dumps(value: Any) -> bytes:
    # default=str so an un-serialisable object in a user's tags costs that field, not the event.
    return json.dumps(value, default=str, separators=(",", ":")).encode()


class Transport:
    def __init__(
        self,
        url: str,
        public_key: str,
        *,
        queue_size: int = 100,
        timeout: float = 5.0,
    ) -> None:
        self._url = url
        self._public_key = public_key
        self._timeout = timeout
        # Bounded on purpose. An unbounded queue turns a burst of errors into the memory leak
        # that finishes the process off.
        self._queue: queue.Queue[bytes | None] = queue.Queue(maxsize=queue_size)
        self._dropped = 0
        self._lock = threading.Lock()

        self._worker = threading.Thread(target=self._run, name="obsly-transport", daemon=True)
        self._worker.start()
        atexit.register(self.close)

    @property
    def dropped(self) -> int:
        """Events discarded because the queue was full — the difference between 'nothing is
        failing' and 'we failed to say so'."""
        with self._lock:
            return self._dropped

    def send(self, envelope: bytes) -> None:
        try:
            self._queue.put_nowait(envelope)
        except queue.Full:
            with self._lock:
                self._dropped += 1
            logger.debug("obsly: queue full, dropped an event (%d total)", self._dropped)

    def flush(self, timeout: float = 2.0) -> bool:
        """Best effort drain. Returns False if the queue did not empty in time."""
        deadline = threading.Event()
        threading.Timer(timeout, deadline.set).start()
        while not self._queue.empty() and not deadline.is_set():
            deadline.wait(0.05)
        return self._queue.empty()

    def close(self) -> None:
        self.flush()
        # A full queue at shutdown means the worker is already busy; it will exit with the
        # process either way.
        with contextlib.suppress(queue.Full):
            self._queue.put_nowait(None)

    def _run(self) -> None:
        while True:
            envelope = self._queue.get()
            if envelope is None:
                return
            try:
                self._post(envelope)
            # Broad on purpose: the worker must survive one bad send. If it dies, the SDK goes
            # silent and the application never learns that it stopped reporting.
            except Exception:
                logger.debug("obsly: failed to deliver an event", exc_info=True)
            finally:
                self._queue.task_done()

    def _post(self, envelope: bytes) -> None:
        request = urllib.request.Request(  # noqa: S310 - scheme validated when the DSN is parsed
            self._url,
            data=envelope,
            method="POST",
            headers={"Content-Type": CONTENT_TYPE, "X-Obsly-Key": self._public_key},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                logger.debug("obsly: delivered, status %s", response.status)
        except urllib.error.HTTPError as exc:
            # 4xx means we are sending something wrong and retrying will not fix it. Say so
            # loudly enough to be findable, because the symptom is otherwise silence.
            logger.warning("obsly: server rejected event with %s %s", exc.code, exc.reason)
        except urllib.error.URLError as exc:
            logger.debug("obsly: could not reach %s (%s)", self._url, exc.reason)
