# Roadmap

One feature per branch, merged into `main` one at a time. A branch lands only when its tests,
linters and type checks are green in CI.

Ordering comes from [user-journey.md](user-journey.md): each planned branch below is a step where
somebody currently hits a wall, not an item from a feature list. That document also carries the
honest statement of **which frameworks the SDKs cover**, which is the question the roadmap is
least able to answer.

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
| 29 | `feat/sdk-browser` | Browser SDK: errors, Core Web Vitals, one trace from paint to query |
| 30 | `feat/browser-demo` | A demo page that reports itself, so the browser half is clickable |
| 31 | `feat/releases` | Per-release health, adoption, and which version introduced an issue |
| 32 | `fix/vitals-never-arrive` | Browser reports were blocked cross-origin by a credentialed beacon |
| 33 | `feat/app-shell` | Sentry-style sidebar and page filters; Insights split by tier |
| 34 | `feat/controls-and-traces` | Global control styling; repeated spans grouped, with timestamps |
| 35 | `feat/db-insights` | Slow-query dashboard, and web vitals with the distribution behind the score |
| 36 | `chore/publish-sdks` | `obsly` on PyPI, `obsly-browser` on npm |
| 37 | `chore/demo-uses-published-sdks` | The demo installs from the registries — which found a dropped parent span |
| 38 | `feat/feature-flags` | FR-CTX-8: flag evaluations on the event, and which flag an issue implicates |
| 39 | `feat/registration` | First-run sign-up, and the door closing behind the first account |
| 40 | `feat/setup-polish` | One setup page for every tier, numbered, with a live "has anything reported" step |
| 41 | `feat/contextual-setup` | An empty layer page offers to instrument its own tier, and says what the other half would join |
| 42 | `feat/journey` | [user-journey.md](user-journey.md); framework coverage stated in the product; no organization question when there is one organization |
| 43 | `feat/sdk-frameworks` | WSGI, Flask and Django integrations; XHR spans and per-route SPA transactions in the browser |
| 44 | `feat/distributed-traces` | One waterfall across projects; outbound propagation; several browser clients on one page |

## Coverage against the reference

`docs/reference/sentry-requirements.md` carries 168 numbered requirements. This is where they
stand, so the gap is a fact rather than a feeling.

| Section | State |
|---|---|
| FR-ERR, FR-GRP — errors, grouping, lifecycle | Built |
| FR-TRC — distributed tracing, browser to database, and across services | Built |
| FR-PERF — N+1 and slow-query detection | Built |
| FR-INS — frontend, backend, database, cache | Built |
| FR-LOG — structured logs | Built |
| FR-SEC — PII scrubbing before the write | Built |
| FR-CTX — context enrichment | Partial. Tags, extra and **feature flags** land; **breadcrumbs**, **user context** and **attachments** do not |
| FR-REL — releases | Partial. Health and adoption land; crash-free *users* needs sessions |
| FR-MET — metrics | Partial. Derived metrics only — see "on metrics" below |
| FR-ALR — alerting | Partial. Webhook delivery; no digests, no native Slack, no ownership routing |
| FR-API — APIs | Partial. REST API; no CLI, no release-tagging CI integration |
| FR-SYM — source maps | None. Browser traces show minified frames — now the largest single gap in the browser story |
| FR-QRY — Discover, query language, custom dashboards | None. Filters are per-page and fixed |
| FR-ORG — teams, roles, per-project access | None. Every signed-in user sees every project |
| FR-QTA — quotas, spike protection | None |
| FR-UPT — uptime and cron monitoring | None |
| FR-RPL, FR-PRF, FR-FBK, FR-AI — replay, profiling, user feedback, Seer | None |

## Planned

Ordered by what unlocks the most. Nothing here is speculative — each is a gap somebody has
already run into.

| Branch | Delivers | Why it matters |
|---|---|---|
| `perf/sdk-delivery` | Batched envelopes, a small worker pool, and dropped events surfaced rather than counted in silence | One POST per event through one thread ceilings at a few hundred a second. Past that the queue fills and events are dropped — the service stays fast and the dashboard quietly stops being true, which is the worse failure. Distributed tracing multiplies the volume, so this follows it immediately |
| `feat/metrics` | Custom counters, gauges and distributions | See "on metrics" below — the shape of this is a real decision, not just work |
| `feat/sessions` | Session reporting, and crash-free users on top of it | Release health reports failure-free *requests* today. Crash-free rate is defined over sessions, and borrowing the name without them would be a number people compare wrongly |
| `feat/teams` | Membership, roles, per-project access | Every signed-in user currently sees every project |
| `feat/quotas` | Per-project rate limits, spike protection | One runaway loop can currently fill the database — and now also fire an alert per event until its cooldown catches it |
| `feat/source-maps` | Upload and apply source maps to browser stack traces | The browser SDK reports minified frames today, and `a.js:1:48291` names no line anyone can open |
| `feat/search` | A query language across issues, spans and logs | Filters are per-page and fixed |
| `feat/breadcrumbs` | The trail of events leading up to a failure | FR-CTX-4. An error says what broke; the breadcrumbs say what the user did to get there |
| `feat/user-context` | `set_user`, and "how many people did this affect" | FR-CTX-2. Issue counts are event counts today, and one user retrying forty times reads as forty people |

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
