"""Obsly SDK for Python.

    import obsly
    obsly.init(dsn="http://<key>@localhost:8081/1", release="myapp@1.0.0")

Zero runtime dependencies — delivery uses urllib from the standard library. An observability
SDK that drags in a HTTP stack is an SDK that can break the application it observes through a
version conflict it had no business causing.
"""

from obsly.client import (
    Client,
    capture_exception,
    capture_message,
    flush,
    get_client,
    init,
    logger,
    set_flag,
    start_transaction,
)
from obsly.integrations import sqlalchemy as sqlalchemy_integration
from obsly.logs import ObslyLogHandler
from obsly.tracing import get_current_span, start_span

__version__ = "0.4.0"

__all__ = [
    "Client",
    "ObslyLogHandler",
    "__version__",
    "capture_exception",
    "capture_message",
    "flush",
    "get_client",
    "get_current_span",
    "init",
    "logger",
    "set_flag",
    "sqlalchemy_integration",
    "start_span",
    "start_transaction",
]
