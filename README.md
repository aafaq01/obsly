# Obsly

Application observability platform — errors, tracing, performance, and release health for the
frontend, the backend, and the database, correlated by design rather than by timestamp guesswork.

Built from first principles: our own wire protocol, our own SDKs, our own ingest pipeline.

## Why

Classic APM hands you three disconnected pillars and leaves correlation to you. Obsly inverts that:
every signal hangs off the same keys — `trace_id`, `issue_id`, `release`, `user_id` — so one click
walks from a browser interaction, to the backend span, to the SQL query, to the commit that
introduced it.

## Status

Early. Building step by step, one feature branch at a time. See [docs/roadmap.md](docs/roadmap.md)
for what has landed and what is next.

[docs/user-journey.md](docs/user-journey.md) walks the path from `docker compose up` to the first
insight, and states plainly **which frameworks each SDK covers and which it does not**. In short:
FastAPI, Django, Flask and any other ASGI or WSGI application on the Python side; any browser
framework on the other, tracing `fetch` and XHR alike and reporting a transaction per route change.

## Architecture

| Layer | Choice | Why |
|---|---|---|
| Backend | Django + Django REST Framework | Multi-tenancy, migrations, RBAC and admin come for free |
| Database | PostgreSQL | Relational core (orgs, projects, issues); JSONB for event payloads |
| Frontend | React + Vite + TypeScript | Fast iteration, typed API client |
| Ingest | Custom NDJSON envelope over HTTP | One request carries many item types; streams without buffering |
| Tests | pytest + Vitest | Backend and frontend gates run independently in CI |

Detail: [docs/architecture.md](docs/architecture.md).

## Reference

[docs/reference/sentry-requirements.md](docs/reference/sentry-requirements.md) is a requirements-level
study of Sentry, used as the functional map for what an observability platform has to cover. It is
reference material, not a specification of this codebase.

## Running the whole stack

Docker only — no Python or Node needed on the host.

```bash
docker compose up --build -d
```

| URL | What |
|---|---|
| **http://localhost:8080** | The app. nginx serves the React bundle and fronts the API on one origin |
| http://localhost:8080/health/ | Liveness + database reachability |
| http://localhost:8080/admin/ | Django admin |
| http://localhost:8000 | Backend directly, for poking the API without nginx |

Migrations run automatically on backend start. `docker compose down` stops it; add `-v` to drop
the database volume too.

**First run.** An install with no users opens on a registration form, and the first account
created owns the instance. Registration then closes: an endpoint that hands out accounts to
whoever can reach it is not a sign-up form for a tool holding your production stack traces,
queries and logs. Set `OBSLY_ALLOW_SIGNUP=True` to keep it open for a team — a decision with a
name rather than a default.

The stack sets `DJANGO_DEBUG=False` for production-like behaviour but `DJANGO_HTTPS=False`,
because it serves plain HTTP — without that, the SSL redirect would bounce every request and
`Secure` cookies would never reach the browser. Never set `DJANGO_HTTPS=False` on anything
internet-facing.

## Instrumenting an application

Two SDKs, one wire protocol and one trace.

**Backend** ([sdk/python](sdk/python)) — zero runtime dependencies:

```bash
pip install obsly
```

```python
import obsly
obsly.init(dsn="https://<public key>@localhost:8081/1", release="api@2026.08.12")
```

**Browser** ([sdk/browser](sdk/browser)) — zero runtime dependencies:

```bash
npm install obsly-browser
```

```ts
import { init } from 'obsly-browser'
init({ dsn: 'https://<public key>@localhost:8081/1', release: 'web@2026.08.12' })
```

The browser SDK puts an `obsly-trace` header on same-origin requests and the Python SDK reads
it, so one waterfall holds the whole story:

```
pageload     /checkout               2000.0ms   (root)
    └─ http.client  POST /api/checkout           900.0ms
http.server  POST /api/checkout       700.0ms   parent = that http.client span
    └─ db.query     SELECT * FROM carts WHERE id = %s   400.0ms
```

That is the point of the project stated in four lines: the paint the reader waited for, the
request it caused, and the query that made it slow, joined by ids rather than by comparing
timestamps across three tools.

**Across services, too.** One project per microservice and per microfrontend, each with its own
DSN — and with trace sharing turned on in their settings, one waterfall holds all of them:

```
Demo App · http.server /checkout/{id}               122ms
    http.client POST http://payments/charge         122ms
    Payments · http.server /charge                  120ms
        db.query UPDATE ledger SET balance = ...    120ms
```

The outbound call there is ordinary `urllib`. The SDK adds the header, the next service
continues the trace, and the page reads the link back out. Sharing is off until each project
turns it on, because joining is a disclosure rather than a default.

## Development

For hot reload, run the services directly. Requires Python 3.12+, Node 22+, and
[uv](https://docs.astral.sh/uv/).

```bash
docker compose up -d postgres               # just the database

cd backend
cp .env.example .env                        # then set DJANGO_SECRET_KEY
uv sync
uv run python manage.py migrate
uv run python manage.py runserver           # :8000

cd ../frontend
npm install
npm run dev                                 # :5173, proxies /api and /health to :8000
```

Open **http://localhost:5173**, not `127.0.0.1` — Vite binds IPv6 `[::1]` only.

Settings deliberately have no fallback for `DJANGO_SECRET_KEY` or `DATABASE_URL` — a missing
value fails at boot rather than silently starting with a shared default.

### Running the gates

CI runs exactly these. Run them before pushing.

```bash
cd backend
uv run ruff check . && uv run ruff format --check .
uv run mypy .
uv run python manage.py makemigrations --check --dry-run
uv run pytest --cov --cov-report=term-missing

cd ../frontend
npm run lint && npm run format:check && npm run typecheck && npm test && npm run build
```

## Licence

MIT — see [LICENSE](LICENSE).
