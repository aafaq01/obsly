"""WSGI middleware — Flask, Bottle, Pyramid, and anything else speaking WSGI.

    import obsly
    from obsly.integrations.wsgi import ObslyMiddleware

    obsly.init(dsn="http://<key>@localhost:8081/1")
    app.wsgi_app = ObslyMiddleware(app.wsgi_app)

Half the Python web world is still WSGI, and until this existed those applications could report
errors they caught themselves and nothing else: no request traces, so no latency, no throughput,
and no way for a browser trace to continue into the server.

Flask has its own integration (`obsly.integrations.flask`) and should use it — from inside the
framework it can name the route it matched, which this cannot. This is the floor: every WSGI
application, no framework knowledge, no imports beyond the standard library.
"""

import contextlib
import logging
from collections.abc import Callable, Iterable, Iterator
from typing import Any
from urllib.parse import unquote

from obsly.client import capture_exception, get_client
from obsly.integrations._http import parameterize, safe_headers, status_label
from obsly.tracing import TRACE_HEADER, parse_trace_header

logger = logging.getLogger("obsly")

Environ = dict[str, Any]
StartResponse = Callable[..., Any]


class ObslyMiddleware:
    def __init__(self, app: Callable[[Environ, StartResponse], Iterable[bytes]]) -> None:
        self.app = app

    def __call__(self, environ: Environ, start_response: StartResponse) -> Iterable[bytes]:
        client = get_client()
        if client is None:
            return self._call_untraced(environ, start_response)

        upstream = parse_trace_header(_header(environ, TRACE_HEADER))
        trace_id, parent_span_id, sampled = upstream or (None, None, None)

        # Entered by hand rather than with `with`, because a WSGI response is not finished when
        # the application returns — it is finished when the iterable it returned is exhausted
        # and closed. Timing a streaming response by the call alone would report a 200ms
        # download as 2ms.
        manager = client.start_transaction(
            _name(environ),
            op="http.server",
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            sampled=sampled,
        )
        transaction = manager.__enter__()
        transaction.data["http.method"] = environ.get("REQUEST_METHOD", "")

        def wrapped_start_response(status: Any, headers: Any, exc_info: Any = None) -> Any:
            code = _status_code(status)
            transaction.status = status_label(code)
            transaction.data["http.status_code"] = code
            return start_response(status, headers, exc_info)

        try:
            result = self.app(environ, wrapped_start_response)
        except BaseException as exc:
            if isinstance(exc, Exception):
                self._report(exc, environ)
            manager.__exit__(type(exc), exc, exc.__traceback__)
            raise

        return _ClosingIterable(result, manager)

    def _call_untraced(self, environ: Environ, start_response: StartResponse) -> Iterable[bytes]:
        try:
            return self.app(environ, start_response)
        except Exception as exc:
            self._report(exc, environ)
            raise

    def _report(self, exc: BaseException, environ: Environ) -> None:
        try:
            client = get_client()
            if client is None:
                return
            capture_exception(exc, extra={"request": request_context(environ, client)})
        # Broad on purpose: a failure to report must never replace the real exception the
        # application is about to raise.
        except Exception:
            logger.debug("obsly: failed to capture a WSGI exception", exc_info=True)


class _ClosingIterable:
    """Yields the application's response through, then closes the transaction.

    A WSGI server is required to call `close()` on the returned iterable, and that — not the
    return of the application callable — is the end of the request.
    """

    def __init__(self, wrapped: Iterable[bytes], manager: Any) -> None:
        self._wrapped = wrapped
        self._manager = manager
        self._closed = False

    def __iter__(self) -> Iterator[bytes]:
        return iter(self._wrapped)

    def close(self) -> None:
        try:
            close = getattr(self._wrapped, "close", None)
            if close is not None:
                close()
        finally:
            self._finish()

    def _finish(self) -> None:
        # Once. A server that closes twice would otherwise send the transaction twice, and the
        # aggregate would count a request that happened once as two.
        if self._closed:
            return
        self._closed = True
        self._manager.__exit__(None, None, None)

    def __del__(self) -> None:
        # A server that never calls close() would otherwise lose the request entirely. Not the
        # path anything should take, but silence is the worse failure. Suppressed rather than
        # logged: an interpreter tearing down has often already closed the logging handlers.
        with contextlib.suppress(Exception):
            self._finish()


def request_context(environ: Environ, client: Any) -> dict[str, Any]:
    """The request, minus anything that authorises or identifies — shared with Flask."""
    path = environ.get("PATH_INFO", "")
    context: dict[str, Any] = {
        "method": environ.get("REQUEST_METHOD", ""),
        "path": path,
        "route": _name(environ),
        "scheme": environ.get("wsgi.url_scheme", ""),
    }

    query = environ.get("QUERY_STRING", "")
    if query and client.send_default_pii:
        context["query_string"] = unquote(query)[:1000]

    context["headers"] = safe_headers(_header_pairs(environ), include_all=client.send_default_pii)

    if client.send_default_pii and environ.get("REMOTE_ADDR"):
        context["client_addr"] = environ["REMOTE_ADDR"]

    return context


def _header_pairs(environ: Environ) -> Iterator[tuple[str, str]]:
    for key, value in environ.items():
        if not isinstance(value, str):
            continue
        if key.startswith("HTTP_"):
            yield key[5:].replace("_", "-").lower(), value
        elif key in ("CONTENT_TYPE", "CONTENT_LENGTH"):
            yield key.replace("_", "-").lower(), value


def _header(environ: Environ, name: str) -> str | None:
    value = environ.get("HTTP_" + name.upper().replace("-", "_"))
    return str(value) if isinstance(value, str) else None


def _name(environ: Environ) -> str:
    """No framework here to ask, so the path with its ids removed is the best available name.

    A framework integration overrides it with the route it actually matched.
    """
    return parameterize(str(environ.get("PATH_INFO", "/")))


def _status_code(status: Any) -> int:
    try:
        return int(str(status).split(" ", 1)[0])
    except (ValueError, TypeError):
        return 200
