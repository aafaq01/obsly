"""Automatic database spans for SQLAlchemy.

    import obsly
    obsly.integrations.sqlalchemy.instrument()

After that every query the application runs appears inside the trace of the request that ran
it, with no `start_span` calls anywhere in the application. That distinction is the whole point:
manual instrumentation only ever covers the code somebody remembered to annotate, and the
queries nobody remembered are exactly the ones that turn out to be the problem.

The statement is recorded as SQLAlchemy hands it over — with bind parameters still as
placeholders. Parameter *values* are never captured: they are the row itself, which is where
the personal data lives.
"""

import logging
from typing import Any

from obsly.tracing import begin_span, end_span

_sdk_log = logging.getLogger("obsly")

MAX_STATEMENT = 2000
_instrumented = False


def instrument() -> bool:
    """Attach to every SQLAlchemy engine, present and future.

    Listening on the Engine *class* rather than an instance means an application that builds
    its engine later, or builds several, is covered without having to hand each one over.

    Returns False when SQLAlchemy is not installed, rather than raising: an optional
    integration that breaks startup by being unavailable is worse than one that is absent.
    """
    global _instrumented

    if _instrumented:
        return True

    try:
        from sqlalchemy import event
        from sqlalchemy.engine import Engine
    except ImportError:
        _sdk_log.debug("obsly: SQLAlchemy is not installed, database spans are off")
        return False

    event.listen(Engine, "before_cursor_execute", _before, retval=False)
    event.listen(Engine, "after_cursor_execute", _after, retval=False)
    event.listen(Engine, "handle_error", _on_error, retval=False)

    _instrumented = True
    return True


def _before(
    conn: Any,
    cursor: Any,
    statement: str,
    parameters: Any,
    context: Any,
    executemany: bool,
) -> None:
    try:
        span = begin_span(
            "db.query",
            _summarise(statement),
            **{
                "db.system": _dialect(conn),
                "db.executemany": bool(executemany),
            },
        )
        # Stashed on the connection, not on self: a module-level dict would be shared across
        # concurrent connections and hand one query's span to another.
        conn.info["_obsly_span"] = span
    except Exception:  # noqa: BLE001
        # Instrumentation must never break the query it is measuring.
        _sdk_log.debug("obsly: could not open a database span", exc_info=True)


def _after(
    conn: Any,
    cursor: Any,
    statement: str,
    parameters: Any,
    context: Any,
    executemany: bool,
) -> None:
    try:
        end_span(conn.info.pop("_obsly_span", None))
    except Exception:  # noqa: BLE001
        _sdk_log.debug("obsly: could not close a database span", exc_info=True)


def _on_error(context: Any) -> None:
    """A failed query still took time, and its span is the only record of how long."""
    try:
        conn = getattr(context, "connection", None)
        if conn is not None:
            end_span(conn.info.pop("_obsly_span", None), "internal_error")
    except Exception:  # noqa: BLE001
        _sdk_log.debug("obsly: could not close a failed database span", exc_info=True)


def _summarise(statement: str) -> str:
    """Collapse whitespace and truncate.

    A multi-line ORM-generated SELECT is unreadable in a one-line waterfall row, and the shape
    of the query is what identifies it — not its indentation.
    """
    collapsed = " ".join(str(statement).split())
    if len(collapsed) > MAX_STATEMENT:
        return collapsed[:MAX_STATEMENT] + " …"
    return collapsed


def _dialect(conn: Any) -> str:
    try:
        return str(conn.dialect.name)
    except Exception:  # noqa: BLE001
        return ""
