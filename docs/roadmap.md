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

## Planned

Ordered by what unlocks the most. Nothing here is speculative — each is a gap somebody has
already run into.

| Branch | Delivers | Why it matters |
|---|---|---|
| `feat/perf-detectors` | N+1, slow query and consecutive-query detection **promoted into Issues** | The Spans page already shows `25.0 calls/req` on a query. A human still has to notice it. A detector opens an issue with the offending query, repeat count and cumulative time wasted — the same triage workflow as an error |
| `feat/alerts` | Issue alerts (new issue, threshold, regression) with Slack/webhook delivery | Nothing currently tells anybody an issue exists. A dashboard nobody has open is not monitoring |
| `feat/metrics` | Custom counters, gauges and distributions | See "on metrics" below — the shape of this is a real decision, not just work |
| `feat/releases` | Release health, crash-free rate, suspect commits | `release` is already on every signal; nothing aggregates by it yet |
| `feat/teams` | Membership, roles, per-project access | Every signed-in user currently sees every project |
| `feat/quotas` | Per-project rate limits, spike protection | One runaway loop can currently fill the database |
| `feat/pii-scrubbing` | Server-side scrubbing rules, default deny | The SDK scrubs; the server trusts what arrives |
| `feat/sdk-browser` | Browser SDK: `onerror`, web vitals, source maps | Everything so far is backend-only |
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
probably right. It is not scheduled ahead of alerts and detectors either way, because a number
nobody is alerted on is a number nobody reads.

## Explicitly out of scope

Host and infrastructure metrics, database server internals, SIEM-scale log archival. Obsly
covers the code-facing half of observability; Prometheus and friends cover the machine-facing
half. See `docs/reference/sentry-requirements.md` § "What Sentry is not" for the same boundary
drawn around the tool this is modelled on.
