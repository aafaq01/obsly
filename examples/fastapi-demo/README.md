# FastAPI demo

A FastAPI app that fails in many different ways, on purpose — so grouping, correlation,
percentiles and the trace waterfall can be *seen* rather than described.

Every endpoint here is a deliberate failure. Nothing in this directory is an example of good
application code.

## Run it

```bash
docker compose up -d                      # Obsly on :8081

cd examples/fastapi-demo
uv venv && uv pip install -e ../../sdk/python fastapi uvicorn

# DSN comes from the project's Settings page in the UI
OBSLY_DSN="http://<key>@localhost:8081/<project_id>" uv run uvicorn main:app --port 8200
```

Then generate traffic:

```bash
uv run python drive.py                    # 60 weighted requests
uv run python drive.py http://127.0.0.1:8200 300
```

## What it produces

| Endpoint | What lands in Obsly |
|---|---|
| `/checkout/c-1` | `ConnectionError` ~half the time, with `db.query` and `http.client` spans |
| `/checkout/c-missing` | `KeyError` — a *second* issue behind the same route, so grouping must separate them |
| `/orders/{id}` | `RuntimeError` **caused by** `TimeoutError` — a chained exception |
| `/report` | 25 identical `db.query` spans then `ZeroDivisionError` — an N+1 you can see in the waterfall |
| `/profile` | `KeyError: 'email'` |
| `/search` | **warnings**, not errors — the request succeeds and something is still recorded |
| `/legacy` | a *handled* exception reported explicitly, caller gets a clean 503 |
| `/slow` | succeeds slowly, so the percentiles have a visible tail |

Traffic is weighted so the stream looks real: two loud issues, several quiet ones, and more
successes than failures. A stream where every issue has the same count tells you nothing about
what to triage first.
