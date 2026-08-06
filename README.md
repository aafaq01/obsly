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

## Development

Requires Python 3.12+, Node 22+, Docker, and [uv](https://docs.astral.sh/uv/).

```bash
docker compose up -d                        # postgres on :5432

cd backend
cp .env.example .env                        # then set DJANGO_SECRET_KEY
uv sync
uv run python manage.py migrate
uv run python manage.py runserver           # :8000

cd ../frontend
npm install
npm run dev                                 # :5173, proxies /api and /health to :8000
```

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
