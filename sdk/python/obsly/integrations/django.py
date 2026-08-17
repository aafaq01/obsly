"""Django integration.

    MIDDLEWARE = [
        "obsly.integrations.django.ObslyMiddleware",
        ...
    ]

First in the list, so it wraps everything below it and sees exceptions the others raise.

Django runs under both WSGI and ASGI, and this covers both — it is a Django middleware, so the
deployment underneath is not its problem. It exists rather than pointing Django at the WSGI
middleware because Django knows the route it resolved (`/orders/<int:pk>/`), and because
`process_exception` sees the exception before Django's own handler turns it into a 500 page.
"""

import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote

from obsly.client import capture_exception, get_client
from obsly.integrations._http import parameterize, safe_headers, status_label
from obsly.tracing import TRACE_HEADER, parse_trace_header

logger = logging.getLogger("obsly")


class ObslyMiddleware:
    def __init__(self, get_response: Callable[[Any], Any]) -> None:
        self.get_response = get_response

    def __call__(self, request: Any) -> Any:
        client = get_client()
        if client is None:
            return self.get_response(request)

        upstream = parse_trace_header(request.headers.get(TRACE_HEADER))
        trace_id, parent_span_id, sampled = upstream or (None, None, None)

        with client.start_transaction(
            _name(request),
            op="http.server",
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            sampled=sampled,
        ) as transaction:
            transaction.data["http.method"] = request.method
            response = self.get_response(request)

            # After the view, because `resolver_match` is only set once Django has resolved the
            # URL — which happens below this middleware, not above it.
            transaction.name = _name(request)
            transaction.status = status_label(response.status_code)
            transaction.data["http.status_code"] = response.status_code
            return response

    def process_exception(self, request: Any, exception: BaseException) -> None:
        """Django calls this before turning the exception into a 500.

        Returning None means "carry on handling it normally" — the application's own error
        handling and the response the client is waiting for are untouched.
        """
        try:
            client = get_client()
            if client is None:
                return None
            capture_exception(exception, extra={"request": _request_context(request, client)})
        # Broad on purpose: a failure to report must never replace the real exception.
        except Exception:
            logger.debug("obsly: failed to capture a Django exception", exc_info=True)
        return None


def _name(request: Any) -> str:
    """The resolved route — `orders/<int:pk>/` — or the path with its ids removed.

    A 404 never resolves, and naming those by raw path would make every probe for
    `/wp-admin.php` its own transaction.
    """
    match = getattr(request, "resolver_match", None)
    route = getattr(match, "route", "") if match is not None else ""
    if route:
        return "/" + str(route).lstrip("/")
    return parameterize(str(getattr(request, "path", "/")))


def _request_context(request: Any, client: Any) -> dict[str, Any]:
    context: dict[str, Any] = {
        "method": getattr(request, "method", ""),
        "path": getattr(request, "path", ""),
        "route": _name(request),
        "scheme": getattr(request, "scheme", ""),
    }

    query = request.META.get("QUERY_STRING", "") if hasattr(request, "META") else ""
    if query and client.send_default_pii:
        context["query_string"] = unquote(query)[:1000]

    context["headers"] = safe_headers(request.headers.items(), include_all=client.send_default_pii)

    if client.send_default_pii and hasattr(request, "META"):
        address = request.META.get("REMOTE_ADDR")
        if address:
            context["client_addr"] = address

    return context
