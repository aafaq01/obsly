# Obsly SDK for Python

Error reporting with **zero runtime dependencies** — delivery uses `urllib` from the standard
library. An observability SDK that drags in an HTTP stack is an SDK that can break the
application it observes through a version conflict it had no business causing.

## Install

```bash
pip install obsly
```

Or from a checkout, to work on the SDK itself:

```bash
pip install -e sdk/python
```

## Use

```python
import obsly

obsly.init(
    dsn="http://<public_key>@localhost:8081/1",  # or set OBSLY_DSN
    release="myapp@1.4.2",
    environment="production",
)
```

A missing or malformed DSN **disables reporting instead of raising**. Nobody wants their service
to refuse to boot because the telemetry endpoint was misconfigured.

### FastAPI / Starlette

```python
from fastapi import FastAPI
from obsly.integrations.fastapi import ObslyMiddleware

app = FastAPI()
app.add_middleware(ObslyMiddleware)
```

Unhandled exceptions are reported and then **re-raised unchanged** — your error handling and the
500 the client receives behave exactly as they would without the SDK.

> If your app registers its own `add_exception_handler(Exception, ...)`, Starlette handles the
> error before any middleware sees it. Call `obsly.capture_exception()` inside that handler too.

### Manual capture

```python
try:
    charge(order)
except PaymentError as exc:
    obsly.capture_exception(exc)

obsly.capture_message("cache rebuild took 40s", level="warning")
```

## Behaviour worth knowing

| | |
|---|---|
| **Non-blocking** | Capture puts an envelope on a bounded queue and returns. A worker thread does the network call. |
| **Bounded queue** | 100 events. An unbounded queue turns a burst of errors into the memory leak that finishes the process off. Drops are counted, not hidden. |
| **Never raises** | Every failure path ends in a log line and a dropped event. |
| **PII off by default** | Query strings, client addresses and most headers are omitted unless `send_default_pii=True`. |
| **Auth headers never sent** | `Authorization`, `Cookie`, `X-Api-Key` and friends are stripped *regardless* of `send_default_pii`. |
| **Route patterns, not paths** | `/items/{id}`, so one issue covers every id rather than one issue per id. |

## Development

```bash
cd sdk/python
uv sync
uv run pytest && uv run ruff check . && uv run mypy obsly
```

## Logs

Off by default — logs are the highest-volume signal by a wide margin.

```python
obsly.init(dsn="...", enable_logs=True)

obsly.logger.info("checkout complete", cart_id="c-1")
```

To forward the logging you already have, without editing a call site:

```python
import logging

logging.getLogger().addHandler(obsly.ObslyLogHandler())
```

Every record carries the active `trace_id` and `span_id`, so a log line and the request that
produced it are joined by an index lookup rather than by scrolling to a timestamp — **including
on requests that succeeded**, which is most of them and where the explanation for the failures
usually lives.

Records are batched and flushed on size, on age, and by a background thread. The thread matters:
the age check only runs when something is added, so without it the last lines of a burst sit
stranded until the application happens to log again.

## Automatic database spans

```python
import obsly

obsly.init(dsn="...", traces_sample_rate=0.1)
obsly.integrations.sqlalchemy.instrument()
```

Every query the application runs now appears inside the trace of the request that ran it, with
no `start_span()` calls anywhere in the application. That distinction is the point: manual
instrumentation only ever covers the code somebody remembered to annotate, and the queries
nobody remembered are exactly the ones that turn out to be the problem.

The statement is recorded as SQLAlchemy hands it over, with bind parameters still as
placeholders. **Parameter values are never captured** — they are the row itself, which is where
the personal data lives.

Returns `False` if SQLAlchemy is not installed rather than raising: an optional integration that
breaks startup by being unavailable is worse than one that is simply absent.
