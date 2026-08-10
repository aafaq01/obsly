"""ASGI middleware — works with FastAPI, Starlette, and anything else speaking ASGI.

    import obsly
    from obsly.integrations.asgi import ObslyMiddleware

    obsly.init(dsn="http://<key>@localhost:8081/1")
    app.add_middleware(ObslyMiddleware)

Install it as the outermost middleware. Anything added after it runs closer to the route, so a
handler raising inside a middleware added later still passes through this one.
"""

import logging
from typing import Any
from urllib.parse import unquote

from obsly.client import capture_exception, get_client
from obsly.tracing import TRACE_HEADER, parse_trace_header

logger = logging.getLogger("obsly")

Scope = dict[str, Any]
Receive = Any
Send = Any

# Headers that identify a person or authorise a request. Never sent, regardless of
# send_default_pii — an SDK that can leak an Authorization header into a third-party store is
# a vulnerability wearing a feature's clothes.
_ALWAYS_STRIP = frozenset(
    {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "x-auth-token"}
)


class ObslyMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        client = get_client()
        if client is None or scope["type"] != "http":
            await self._call_untraced(scope, receive, send)
            return

        upstream = parse_trace_header(_header(scope, TRACE_HEADER))
        trace_id, parent_span_id, sampled = upstream or (None, None, None)

        # The name is filled in after routing: the matched pattern only exists on the scope
        # once Starlette has resolved it, and a raw path here would make every id its own row.
        with client.start_transaction(
            _path(scope),
            op="http.server",
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            sampled=sampled,
        ) as transaction:
            status_holder: dict[str, int] = {}

            async def send_wrapper(message: dict[str, Any]) -> None:
                if message["type"] == "http.response.start":
                    status_holder["status"] = message["status"]
                await send(message)

            try:
                await self.app(scope, receive, send_wrapper)
            except Exception as exc:
                transaction.status = "internal_error"
                transaction.name = _route_pattern(scope) or _path(scope)
                # Report, then re-raise unchanged. The application's own error handling — and
                # the 500 the client is waiting for — must behave exactly as it would
                # without us.
                self._report(exc, scope)
                raise
            else:
                transaction.name = _route_pattern(scope) or _path(scope)
                transaction.status = _status_label(status_holder.get("status", 200))
                transaction.data["http.status_code"] = status_holder.get("status", 200)
                transaction.data["http.method"] = scope.get("method", "")

    async def _call_untraced(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await self.app(scope, receive, send)
        except Exception as exc:
            self._report(exc, scope)
            raise

    def _report(self, exc: BaseException, scope: Scope) -> None:
        try:
            client = get_client()
            if client is None:
                return
            capture_exception(exc, extra={"request": self._request_context(scope, client)})
        # Broad on purpose: a failure to report must never replace the real exception the
        # application is about to raise.
        except Exception:
            logger.debug("obsly: failed to capture an ASGI exception", exc_info=True)

    def _request_context(self, scope: Scope, client: Any) -> dict[str, Any]:
        path = scope.get("path", "")
        context: dict[str, Any] = {
            "method": scope.get("method", ""),
            "path": path,
            # The matched route pattern, not the concrete path — "/items/{id}" groups, while
            # "/items/8123" makes a separate issue for every id ever requested.
            "route": _route_pattern(scope) or path,
            "scheme": scope.get("scheme", ""),
            "http_version": scope.get("http_version", ""),
        }

        query = scope.get("query_string", b"")
        if query and client.send_default_pii:
            context["query_string"] = unquote(query.decode("utf-8", "replace"))[:1000]

        context["headers"] = _headers(scope, include_all=client.send_default_pii)

        if client.send_default_pii:
            clientinfo = scope.get("client")
            if clientinfo:
                context["client_addr"] = clientinfo[0]

        return context


def _route_pattern(scope: Scope) -> str:
    """FastAPI and Starlette stash the matched route on the scope once routing has happened."""
    route = scope.get("route")
    return str(getattr(route, "path", "") or "")


def _headers(scope: Scope, *, include_all: bool) -> dict[str, str]:
    safe = {}
    for raw_name, raw_value in scope.get("headers", []):
        name = raw_name.decode("latin-1").lower()
        if name in _ALWAYS_STRIP:
            continue
        if not include_all and name not in ("content-type", "user-agent", "host", "referer"):
            continue
        safe[name] = raw_value.decode("latin-1")[:500]
    return safe


def _header(scope: Scope, name: str) -> str | None:
    wanted = name.lower().encode("latin-1")
    for raw_name, raw_value in scope.get("headers", []):
        if raw_name.lower() == wanted:
            return str(raw_value.decode("latin-1"))
    return None


def _path(scope: Scope) -> str:
    return str(scope.get("path", "/"))


def _status_label(status: int) -> str:
    """gRPC-style labels, so "did it work" is one field rather than a numeric range check."""
    if status < 400:
        return "ok"
    if status == 404:
        return "not_found"
    if status in (401, 403):
        return "unauthenticated"
    if status < 500:
        return "invalid_argument"
    return "internal_error"
