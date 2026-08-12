"""Spans and transactions.

A transaction is one service's participation in a trace — the root span of the work it did.
Spans nest inside it and describe where the time actually went.

Two rules shape the design:

1. **The sampling decision is made once, at the head of the trace, and propagated.** Deciding
   per service would produce traces with holes in the middle, which are worse than no trace:
   they look like the missing service was never called.
2. **A transaction name is a route pattern, never a URL.** `/users/{id}` aggregates;
   `/users/8123` makes a separate row for every id ever requested and a percentile of one.
"""

import contextvars
import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# Contextvars, not thread-locals: an async framework runs many requests on one thread, and a
# thread-local span would leak one request's timings into another's trace.
_current_span: contextvars.ContextVar["Span | None"] = contextvars.ContextVar(
    "obsly_current_span", default=None
)

TRACE_HEADER = "obsly-trace"
MAX_SPANS = 1000


def _trace_id() -> str:
    return secrets.token_hex(16)


def _span_id() -> str:
    return secrets.token_hex(8)


@dataclass
class Span:
    op: str
    description: str = ""
    trace_id: str = field(default_factory=_trace_id)
    span_id: str = field(default_factory=_span_id)
    parent_span_id: str | None = None
    status: str = "ok"
    data: dict[str, Any] = field(default_factory=dict)

    start: float = field(default_factory=time.time)
    end: float | None = None
    sampled: bool = True

    # Set by begin_span so end_span can restore the previous context. Unused by the context
    # manager, which holds its own token on the stack.
    _token: Any = None

    # Only the root holds the list; a nested span appends to its transaction rather than
    # carrying children, so serialising is a flat walk instead of a recursive one.
    transaction: "Transaction | None" = None

    def finish(self, status: str | None = None) -> None:
        if status is not None:
            self.status = status
        if self.end is None:
            self.end = time.time()

    @property
    def duration_ms(self) -> float:
        return ((self.end or time.time()) - self.start) * 1000

    def to_payload(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "trace_id": self.trace_id,
            "op": self.op,
            "description": self.description[:2000],
            "status": self.status,
            "start_timestamp": datetime.fromtimestamp(self.start, tz=UTC).isoformat(),
            "timestamp": datetime.fromtimestamp(self.end or time.time(), tz=UTC).isoformat(),
            "data": self.data,
        }


@dataclass
class Transaction(Span):
    name: str = "<unnamed>"
    spans: list[Span] = field(default_factory=list)
    dropped_spans: int = 0

    def add(self, span: Span) -> None:
        # Bounded: one runaway loop emitting spans must cost a truncated trace, not the memory
        # of the process being observed.
        if len(self.spans) >= MAX_SPANS:
            self.dropped_spans += 1
            return
        self.spans.append(span)

    def to_payload(self) -> dict[str, Any]:
        return {
            "type": "transaction",
            "transaction": self.name,
            "start_timestamp": datetime.fromtimestamp(self.start, tz=UTC).isoformat(),
            "timestamp": datetime.fromtimestamp(self.end or time.time(), tz=UTC).isoformat(),
            "contexts": {
                "trace": {
                    "trace_id": self.trace_id,
                    "span_id": self.span_id,
                    # The span upstream was in when it called us. Parsed from the incoming
                    # header and then dropped here, so a browser page load and the request it
                    # made shared a trace id and lost the link between them — with several
                    # requests in flight, nothing said which fetch caused which.
                    "parent_span_id": self.parent_span_id,
                    "op": self.op,
                    "status": self.status,
                    "description": self.description,
                }
            },
            "spans": [span.to_payload() for span in self.spans],
            "dropped_spans": self.dropped_spans,
            "data": self.data,
        }


def get_current_span() -> Span | None:
    return _current_span.get()


def parse_trace_header(value: str | None) -> tuple[str, str, bool] | None:
    """`<trace_id>-<parent_span_id>-<sampled>`.

    Returns None on anything malformed rather than raising: a broken header from an upstream we
    do not control must start a new trace, never fail the request carrying it.
    """
    if not value:
        return None

    parts = value.strip().split("-")
    if len(parts) != 3:
        return None

    trace_id, parent_span_id, sampled = parts
    if len(trace_id) != 32 or len(parent_span_id) != 16:
        return None
    if not _is_hex(trace_id) or not _is_hex(parent_span_id):
        return None

    return trace_id, parent_span_id, sampled == "1"


def _is_hex(value: str) -> bool:
    return all(c in "0123456789abcdefABCDEF" for c in value)


def trace_header(span: Span) -> str:
    return f"{span.trace_id}-{span.span_id}-{'1' if span.sampled else '0'}"


def begin_span(op: str, description: str = "", **data: Any) -> Span | None:
    """Open a span without a `with` block.

    Instrumentation that hooks a library's before/after callbacks cannot hold a context
    manager open across them, so it needs the two halves separately. Returns None when there
    is nothing to attach to, and callers must treat that as "do nothing" rather than an error.
    """
    parent = _current_span.get()
    if parent is None or parent.transaction is None or not parent.sampled:
        return None

    span = Span(
        op=op,
        description=description,
        trace_id=parent.trace_id,
        parent_span_id=parent.span_id,
        data=data,
        sampled=parent.sampled,
        transaction=parent.transaction,
    )
    span._token = _current_span.set(span)
    return span


def end_span(span: Span | None, status: str | None = None) -> None:
    """Close a span opened with begin_span. Safe to call with None."""
    if span is None:
        return

    span.finish(status)
    token = getattr(span, "_token", None)
    if token is not None:
        _current_span.reset(token)
        span._token = None

    if span.transaction is not None:
        span.transaction.add(span)


@contextmanager
def start_span(op: str, description: str = "", **data: Any) -> Iterator[Span]:
    """Time a unit of work inside the active transaction.

    A no-op when there is no active transaction — instrumentation in a library must not depend
    on whether the application happened to enable tracing.
    """
    parent = _current_span.get()
    if parent is None or parent.transaction is None:
        yield Span(op=op, description=description, sampled=False)
        return

    span = Span(
        op=op,
        description=description,
        trace_id=parent.trace_id,
        parent_span_id=parent.span_id,
        data=data,
        sampled=parent.sampled,
        transaction=parent.transaction,
    )
    token = _current_span.set(span)
    try:
        yield span
    except Exception:
        span.finish("internal_error")
        raise
    else:
        span.finish()
    finally:
        _current_span.reset(token)
        if span.sampled:
            parent.transaction.add(span)
