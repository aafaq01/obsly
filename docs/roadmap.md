# Roadmap

One feature per branch, merged into `main` one at a time. A branch lands only when its tests,
linters and type checks are green in CI.

## Milestone 1 — an error reaches a screen

| # | Branch | Delivers | Status |
|---|---|---|---|
| 1 | `feat/scaffold` | Django + DRF backend, React + Vite frontend, ruff / mypy / pytest / eslint / vitest, CI | ☑ |
| 1b | `feat/docker-stack` | Whole stack under `docker compose up` behind one origin on :8080 | ☑ |
| 2 | `feat/core-domain` | Organization, Team, Project, Membership, ProjectKey (DSN), admin, tests | ☐ |
| 3 | `feat/ingest` | Envelope protocol, DSN auth, event storage, rate limiting | ☐ |
| 4 | `feat/grouping` | Fingerprinting, Issue dedupe, first/last seen, counts | ☐ |
| 5 | `feat/read-api` | Issue list/detail, event detail, search filters | ☐ |
| 6 | `feat/web-issues` | React issue stream + issue detail with stack trace | ☐ |
| 7 | `feat/sdk-python` | `obsly-sdk` — capture, scope, breadcrumbs, async transport | ☐ |
| 8 | `feat/sdk-browser` | Browser SDK — global handlers, breadcrumbs, offline queue | ☐ |

## Milestone 2 — context and workflow

| # | Branch | Delivers |
|---|---|---|
| 9 | `feat/auth` | Registration, login, sessions, org invitations |
| 10 | `feat/rbac` | Roles, team scoping, permission enforcement across the API |
| 11 | `feat/issue-workflow` | Resolve / archive / assign, regression detection |
| 12 | `feat/releases` | Releases, deploys, release health, crash-free rate |
| 13 | `feat/alerts` | Issue alert rules, notification delivery |
| 14 | `feat/feature-flags` | Flag evaluation service — gates our own rollout, then ships as a product surface |

## Milestone 3 — performance

| # | Branch | Delivers |
|---|---|---|
| 15 | `feat/tracing` | Traces, spans, propagation headers, trace waterfall |
| 16 | `feat/db-insights` | Query capture, parameterisation, per-query aggregates |
| 17 | `feat/perf-detectors` | N+1 detection, slow query, consecutive queries |
| 18 | `feat/web-vitals` | LCP / CLS / INP / FCP / TTFB capture and scoring |

## Milestone 4 — operations

| # | Branch | Delivers |
|---|---|---|
| 19 | `feat/quotas` | Per-project quotas, rate limits, spike protection |
| 20 | `feat/pii-scrubbing` | Server-side scrubbing rules, default deny |
| 21 | `feat/billing` | Plans, usage metering, spend caps |
| 22 | `feat/sourcemaps` | Debug IDs, source map upload and resolution |

## Explicitly out of scope

Host and infrastructure metrics, database server internals, SIEM-scale log archival. Obsly covers
the code-facing half of observability; Prometheus and friends cover the machine-facing half.
