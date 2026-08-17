"""Flask integration.

    import obsly
    from obsly.integrations.flask import instrument

    obsly.init(dsn="http://<key>@localhost:8081/1", release="api@1.0.0")
    app = Flask(__name__)
    instrument(app)

The WSGI middleware would work here too, and this exists for one reason: from inside Flask the
matched rule is available, so a transaction is named `/orders/<int:id>` rather than
`/orders/{id}` guessed from the path. The framework's own name is right even when an id looks
like a word, and it is the name the code is written in.

Errors are taken from the `got_request_exception` signal rather than an error handler, because
an integration that installs a handler changes what the application returns. Reporting must not
alter behaviour.
"""

import logging
from typing import Any

from flask import Flask, g, got_request_exception, request

from obsly.client import capture_exception, get_client
from obsly.integrations._http import parameterize, status_label
from obsly.integrations.wsgi import request_context
from obsly.tracing import TRACE_HEADER, parse_trace_header

logger = logging.getLogger("obsly")

_MANAGER = "_obsly_manager"
_TRANSACTION = "_obsly_transaction"


def instrument(app: Flask) -> None:
    """Report errors and trace requests for one Flask application."""
    app.before_request(_before_request)
    app.after_request(_after_request)
    app.teardown_request(_teardown_request)
    got_request_exception.connect(_on_exception, app)


def _before_request() -> None:
    client = get_client()
    if client is None:
        return

    try:
        upstream = parse_trace_header(request.headers.get(TRACE_HEADER))
        trace_id, parent_span_id, sampled = upstream or (None, None, None)

        manager = client.start_transaction(
            _name(),
            op="http.server",
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            sampled=sampled,
        )
        transaction = manager.__enter__()
        transaction.data["http.method"] = request.method
        setattr(g, _MANAGER, manager)
        setattr(g, _TRANSACTION, transaction)
    # Broad on purpose: an SDK that can 500 a request by failing to measure it is worse than
    # no SDK.
    except Exception:
        logger.debug("obsly: failed to start a Flask transaction", exc_info=True)


def _after_request(response: Any) -> Any:
    transaction = getattr(g, _TRANSACTION, None)
    if transaction is not None:
        transaction.status = status_label(response.status_code)
        transaction.data["http.status_code"] = response.status_code
        # Now that routing has happened, the rule is known — before_request only sees it when
        # the URL map matched before the hook, which is not guaranteed for every dispatch path.
        transaction.name = _name()
    return response


def _teardown_request(exc: BaseException | None) -> None:
    manager = getattr(g, _MANAGER, None)
    if manager is None:
        return
    g.pop(_MANAGER, None)
    transaction = g.pop(_TRANSACTION, None)

    if exc is not None and transaction is not None:
        transaction.status = "internal_error"

    try:
        manager.__exit__(None, None, None)
    except Exception:
        logger.debug("obsly: failed to finish a Flask transaction", exc_info=True)


def _on_exception(sender: Any, exception: BaseException, **extra: Any) -> None:
    try:
        client = get_client()
        if client is None:
            return
        capture_exception(exception, extra={"request": request_context(request.environ, client)})
    except Exception:
        logger.debug("obsly: failed to capture a Flask exception", exc_info=True)


def _name() -> str:
    """The matched rule — `/orders/<int:id>` — or the path with its ids removed if none matched.

    A 404 has no rule, and naming those by path would make every probe for `/wp-admin.php` its
    own transaction.
    """
    rule = request.url_rule
    if rule is not None and rule.rule:
        return str(rule.rule)
    return parameterize(request.path)
