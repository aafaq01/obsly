"""The other end of distributed tracing: the call this service makes.

Continuing an incoming trace was never the hard half — every server integration already does
it. The half that was missing is the one that makes a *chain* possible: when this service calls
another, the trace has to travel with the request, or it stops here and every hop beyond it is
an unrelated row in somebody else's project.

Three clients are patched, because between them they are how Python makes an HTTP request:
`httpx`, `requests`, and `urllib` underneath both of them and everything else.

**Nothing is imported to patch it.** `instrument()` looks in `sys.modules` and patches what is
already there. An SDK that imports `requests` to instrument it has added a dependency to a
process that had chosen not to have one, and paid its import cost on every start.
"""

import logging
import sys
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from obsly.tracing import (
    TRACE_HEADER,
    begin_span,
    end_span,
    get_current_span,
    trace_header,
)

logger = logging.getLogger("obsly")

_undo: list[Callable[[], None]] = []


def instrument() -> None:
    """Patch whichever HTTP clients this process has already imported.

    Called from `obsly.init()`. Safe to call twice — a second call is a no-op rather than a
    double patch, which would produce two spans and two headers per request.
    """
    if _undo:
        return

    for patch in (_patch_httpx, _patch_requests, _patch_urllib):
        try:
            patch()
        # Broad on purpose: a client library whose internals moved must cost its own spans, not
        # the application's ability to start.
        except Exception:
            logger.debug("obsly: could not instrument an HTTP client", exc_info=True)


def uninstrument() -> None:
    """Put every patched function back. Exists for tests, and for anyone who wants out."""
    while _undo:
        _undo.pop()()


def _describe(method: str, url: str) -> tuple[str, dict[str, Any]]:
    """`POST https://payments.internal/charge` — and the parts, separately, as span data.

    The query string is dropped: it is the part of a URL that carries session tokens and email
    addresses, and a span description is stored and displayed.
    """
    parts = urlsplit(url)
    clean = f"{parts.scheme}://{parts.netloc}{parts.path}" if parts.scheme else url
    return f"{method.upper()} {clean}", {
        "http.method": method.upper(),
        "http.url": clean,
        "server.address": parts.netloc,
    }


def _start(method: str, url: str) -> Any:
    description, data = _describe(method, url)
    return begin_span("http.client", description, **data)


def _finish(span: Any, status: int | None) -> None:
    """The call came back. `status` is None when the client did not expose one — which is not
    the same as failing, and mapping it to a failure would paint every such call red.

    end_span, not span.finish: finishing alone leaves the span out of the transaction it
    belongs to and leaves it current, so the next span in the request would hang off a call
    that has already returned.
    """
    if span is None:
        return
    if status is not None:
        span.data["http.status_code"] = status
    end_span(span, "ok" if status is None or status < 400 else "internal_error")


def _failed(span: Any) -> None:
    """The call raised: refused, timed out, DNS, TLS. No status, and the most interesting kind
    of span there is — it is the gap the waterfall would otherwise have."""
    if span is not None:
        end_span(span, "internal_error")


def _header_value(span: Any) -> str | None:
    """The header a downstream service will continue the trace from.

    Falls back to the current span when there is no client span — which happens when tracing is
    on but this call is outside any transaction. There is nothing to continue then, and sending
    a header pointing at a span the collector will never see would give the receiving service a
    parent that does not exist.
    """
    active = span or get_current_span()
    return trace_header(active) if active is not None else None


def _patch_httpx() -> None:
    httpx = sys.modules.get("httpx")
    if httpx is None:
        return

    original_send = httpx.Client.send
    original_async_send = httpx.AsyncClient.send

    def send(self: Any, request: Any, **kwargs: Any) -> Any:
        span = _start(request.method, str(request.url))
        header = _header_value(span)
        if header:
            request.headers[TRACE_HEADER] = header
        try:
            response = original_send(self, request, **kwargs)
        except BaseException:
            _failed(span)
            raise
        _finish(span, response.status_code)
        return response

    async def async_send(self: Any, request: Any, **kwargs: Any) -> Any:
        span = _start(request.method, str(request.url))
        header = _header_value(span)
        if header:
            request.headers[TRACE_HEADER] = header
        try:
            response = await original_async_send(self, request, **kwargs)
        except BaseException:
            _failed(span)
            raise
        _finish(span, response.status_code)
        return response

    httpx.Client.send = send
    httpx.AsyncClient.send = async_send

    def undo() -> None:
        httpx.Client.send = original_send
        httpx.AsyncClient.send = original_async_send

    _undo.append(undo)


def _patch_requests() -> None:
    requests = sys.modules.get("requests")
    if requests is None:
        return

    original = requests.Session.send

    def send(self: Any, request: Any, **kwargs: Any) -> Any:
        span = _start(request.method or "GET", str(request.url))
        header = _header_value(span)
        if header:
            request.headers[TRACE_HEADER] = header
        try:
            response = original(self, request, **kwargs)
        except BaseException:
            _failed(span)
            raise
        _finish(span, response.status_code)
        return response

    requests.Session.send = send
    _undo.append(lambda: setattr(requests.Session, "send", original))


def _patch_urllib() -> None:
    """`urllib.request.urlopen`, which is what everything else is built on.

    Including our own transport — which is why the span is skipped for requests to the
    collector: an SDK that traces its own delivery would report a span describing the report,
    and then a span describing that.
    """
    import urllib.request

    original = urllib.request.AbstractHTTPHandler.do_open

    def do_open(self: Any, http_class: Any, req: Any, **kwargs: Any) -> Any:
        url = req.full_url
        if _is_collector(req):
            return original(self, http_class, req, **kwargs)

        span = _start(req.get_method(), url)
        header = _header_value(span)
        if header:
            req.add_unredirected_header(TRACE_HEADER, header)
        try:
            response = original(self, http_class, req, **kwargs)
        except BaseException:
            _failed(span)
            raise
        _finish(span, getattr(response, "status", None))
        return response

    # setattr rather than assignment: replacing a method on a class is exactly what patching
    # is, and mypy is right to flag the direct form — it is only correct because the shape
    # matches, which is a promise this module keeps rather than one the type system can.
    setattr(urllib.request.AbstractHTTPHandler, "do_open", do_open)  # noqa: B010
    _undo.append(lambda: setattr(urllib.request.AbstractHTTPHandler, "do_open", original))


def _is_collector(req: Any) -> bool:
    return bool(req.get_header(_CONTENT_TYPE_HEADER) == _ENVELOPE_CONTENT_TYPE)


# Matches the transport's own Content-Type. Recognising our own delivery by its content type
# rather than by URL means a collector behind any hostname is still not traced.
_CONTENT_TYPE_HEADER = "Content-type"
_ENVELOPE_CONTENT_TYPE = "application/x-obsly-envelope"
