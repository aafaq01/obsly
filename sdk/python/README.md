# Obsly SDK for Python

Error reporting with **zero runtime dependencies** — delivery uses `urllib` from the standard
library. An observability SDK that drags in an HTTP stack is an SDK that can break the
application it observes through a version conflict it had no business causing.

## Install

```bash
pip install -e sdk/python
```

## Use

```python
import obsly

obsly.init(
    dsn="http://<public_key>@localhost:8081/1",   # or set OBSLY_DSN
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
