# Roadmap

One feature per branch, merged into `main` one at a time. A branch lands only when its tests,
linters and type checks are green in CI.

## Shipped

| # | Branch | Delivers |
|---|---|---|
| 1 | `feat/scaffold` | Django + DRF, React + Vite, ruff / mypy / pytest / eslint / vitest, CI |
| 2 | `feat/docker-stack` | Whole stack under `docker compose up`, one origin on :8081 |
| 3 | `feat/core-domain` | Organization, Project, ProjectKey (DSN) |
| 4 | `feat/ingest` | NDJSON envelope protocol, DSN auth, event storage |
| 5 | `feat/sdk-python` | Zero-dependency SDK, FastAPI/ASGI middleware |
| 6 | `feat/grouping` | Fingerprinting, issue dedupe, regression detection |
| 7 | `feat/web-issues` | Issue stream and issue detail with stack traces |
| 8 | `feat/issue-workflow` | Resolve / ignore / reopen |
| 9 | `feat/auth` | Own login, throttled; the admin is off the path |
| 10 | `feat/web-projects` | Projects, DSN keys, organizations — all in the UI |
| 11 | `feat/tracing` | Spans, transactions, trace propagation, sampling |
| 12 | `feat/perf-ui` | p50/p75/p95/p99, throughput, failure rate per endpoint |
| 13 | `feat/traces-and-nav` | Trace list, waterfall, project tabs |
| 14 | `feat/correlation` | Errors ↔ traces, joined by `trace_id` |
| 15 | `feat/logs` | Structured logs, stdlib `logging` bridge, log viewer |
| 16 | `feat/db-spans` | Automatic SQLAlchemy query spans |
| 17 | `feat/span-insights` | Aggregate span view, project dashboard |
| 18 | `fix/navigation` | Project tabs reachable; `/` stops opening an arbitrary project |
| 19 | `feat/perf-detectors` | N+1, slow query and consecutive-query detection, promoted into Issues |
| 20 | `feat/time-ranges` | Minute- and second-resolution windows, one picker everywhere |
| 21 | `feat/log-search` | Level filters and indexed search across log bodies |
| 22 | `feat/instrument-ui` | Monospace for measured values; the instrument-panel direction |
| 23 | `feat/span-detail` | One span group: distribution, callers, traces to open |
| 24 | `fix/navigation-depth` | Breadcrumbs, trace back links, project switcher, rank charts |
| 25 | `feat/pii-scrubbing` | Server-side secret redaction, deny by default, before the write |
| 26 | `feat/endpoint-detail` | Clickable endpoints: where one endpoint's time actually goes |
| 27 | `feat/chart-timestamps` | Charts read in clock time and say what they measure |
| 28 | `feat/alerts` | New-issue, regression and frequency rules with webhook delivery |

## Planned

Ordered by what unlocks the most. Nothing here is speculative — each is a gap somebody has
already run into.

| Branch | Delivers | Why it matters |
|---|---|---|
| `feat/metrics` | Custom counters, gauges and distributions | See "on metrics" below — the shape of this is a real decision, not just work |
| `feat/releases` | Release health, crash-free rate, suspect commits | `release` is already on every signal; nothing aggregates by it yet |
| `feat/teams` | Membership, roles, per-project access | Every signed-in user currently sees every project |
| `feat/quotas` | Per-project rate limits, spike protection | One runaway loop can currently fill the database — and now also fire an alert per event until its cooldown catches it |
| `feat/sdk-browser` | Browser SDK: `onerror`, web vitals, source maps | Everything so far is backend-only. This is the next one: an error in the browser and the request it made are currently two unrelated facts |
| `feat/search` | A query language across issues, spans and logs | Filters are per-page and fixed |

## On metrics

**Derived metrics exist today.** Throughput, failure rate, latency percentiles, log volume,
error counts and per-span aggregates are all computed from stored events and spans, and they
power the dashboard. Nothing extra had to be emitted to get them.

**Custom metrics — a counter or gauge you emit yourself — do not exist.** That is a deliberate
ordering rather than an oversight, and the reason is worth recording: Sentry shipped custom
metrics, then withdrew them, on the grounds that a numeric attribute on a span answers the same
question with one signal instead of two. `start_span(attributes={"items_in_cart": 12})` is
already a metric emission; it is queryable, and unlike a bare counter it arrives attached to the
request that produced it.

So `feat/metrics` is planned, but the design question comes first: whether to build a separate
metrics pillar with its own storage and cardinality limits, or to make span attributes
aggregatable and get most of the value for a fraction of the surface area. The second is
probably right. It was not scheduled ahead of alerts and detectors either way, because a
number nobody is alerted on is a number nobody reads — and now that alerting exists, a metric
would have something to be alerted on.

## Explicitly out of scope

Host and infrastructure metrics, database server internals, SIEM-scale log archival. Obsly
covers the code-facing half of observability; Prometheus and friends cover the machine-facing
half. See `docs/reference/sentry-requirements.md` § "What Sentry is not" for the same boundary
drawn around the tool this is modelled on.
