# Architecture

## Shape

```
  SDK (python / browser)          Obsly server                     UI
  ─────────────────────           ────────────                     ──
  capture ──► scope ──► queue ──► POST /api/{project}/envelope/
                        (async)      │
                                     │ DSN auth, size + rate limit
                                     ▼
                              envelope parser  (NDJSON, streamed)
                                     │
                                     ├─ event      ──► normalise ──► fingerprint ──► Issue
                                     ├─ transaction ─► spans
                                     └─ session    ──► release health
                                     │
                                     ▼
                                 PostgreSQL ──────────────────► DRF read API ──► React
```

## Decisions

### Django over FastAPI

Obsly is a multi-tenant product before it is a data pipeline. Organizations, teams, members,
role-based access, project settings, quota accounting, an audit log and an admin surface are all
table stakes, and Django ships them. The ingest hot path is a small, isolated part of the codebase;
optimising it later is a bounded problem, whereas hand-rolling multi-tenancy is not.

### PostgreSQL first, columnar later

Issues, projects and memberships are relational and low-volume — Postgres is the right home
permanently. Events and spans are high-volume and append-only, and will eventually want a columnar
store. They start in Postgres with JSONB payloads because a working system beats a fast one that
does not exist. The read path goes through a query layer so the storage swap stays contained.

### NDJSON envelopes, not one-event-per-request

An envelope is a header line followed by pairs of `(item header, item payload)` lines:

```
{"event_id":"9f8e...","sent_at":"2026-08-06T10:00:00Z","dsn":"..."}
{"type":"event","length":812}
{"level":"error","exception":{...},"breadcrumbs":[...]}
{"type":"session","length":142}
{"sid":"...","status":"crashed",...}
```

Why this shape:

- **One request, many item types.** An SDK flush sends the error, the session update and the
  attachment together instead of three round trips.
- **Streamable.** The server reads item headers and dispatches payload-by-payload; a 10 MB envelope
  never has to be fully materialised or JSON-parsed as one document.
- **Item-level rejection.** An oversized attachment is dropped without discarding the error next
  to it in the same envelope.
- **Forward compatible.** An unknown `type` is skipped rather than failing the request, so old
  servers accept new SDKs.

### The DSN

`https://<public_key>@<host>/<project_id>` — a write-only credential, safe to embed in a browser
bundle. It authenticates the project and nothing else: it cannot read a single event back.

### Correlation is the schema, not a join

`trace_id`, `release`, `environment` and `user` are columns on every signal, populated by the SDK
from ambient scope. Correlating an error with the span that produced it is an index lookup, not a
timestamp heuristic.

## Layout

```
backend/
  config/          Django project — settings (base/dev/test/prod), urls, asgi, wsgi
  apps/            One Django app per bounded context, added as features land
  tests/           pytest, mirroring apps/
frontend/
  src/             React + TypeScript
docs/              Architecture, roadmap, reference material
```
