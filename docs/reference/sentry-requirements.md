# Sentry — Functional & Technical Requirements Document

**Status:** Reference specification
**Subject:** Sentry (SaaS + self-hosted), application observability platform
**Purpose:** Describe, at requirement level, everything Sentry does — every product surface, the data model behind it, the ingestion mechanics, and how each of frontend, backend, database, mobile and infrastructure is instrumented end to end.

> **Accuracy note.** Sentry ships fast and reorganises products often (Metrics, Logs and the transaction→span billing model have all changed materially in the last two years). Requirements below reflect the platform as understood through ~2026. Anything marked ⚠️ is version-sensitive and should be re-checked against `docs.sentry.io` before being used as an acceptance criterion.

---

## Table of contents

1. [What Sentry is](#1-what-sentry-is)
2. [Core domain model](#2-core-domain-model)
3. [Ingestion & processing pipeline](#3-ingestion--processing-pipeline)
4. [FR-ERR — Error monitoring](#4-fr-err--error-monitoring)
5. [FR-GRP — Grouping and issue lifecycle](#5-fr-grp--grouping-and-issue-lifecycle)
6. [FR-CTX — Context enrichment](#6-fr-ctx--context-enrichment)
7. [FR-SYM — Source maps, debug symbols, symbolication](#7-fr-sym--source-maps-debug-symbols-symbolication)
8. [FR-TRC — Distributed tracing](#8-fr-trc--distributed-tracing)
9. [FR-PERF — Performance issue detection](#9-fr-perf--performance-issue-detection)
10. [FR-INS — Insights modules](#10-fr-ins--insights-modules)
11. [FR-RPL — Session Replay](#11-fr-rpl--session-replay)
12. [FR-PRF — Profiling](#12-fr-prf--profiling)
13. [FR-LOG — Logs](#13-fr-log--logs)
14. [FR-MET — Metrics](#14-fr-met--metrics)
15. [FR-UPT — Uptime and Cron monitoring](#15-fr-upt--uptime-and-cron-monitoring)
16. [FR-REL — Releases, deploys, release health](#16-fr-rel--releases-deploys-release-health)
17. [FR-ALR — Alerting and notifications](#17-fr-alr--alerting-and-notifications)
18. [FR-QRY — Discover, dashboards, query language](#18-fr-qry--discover-dashboards-query-language)
19. [FR-FBK — User feedback](#19-fr-fbk--user-feedback)
20. [FR-AI — Seer and AI features](#20-fr-ai--seer-and-ai-features)
21. [FR-INT — Integrations](#21-fr-int--integrations)
22. [FR-ORG — Organisation, access control, admin](#22-fr-org--organisation-access-control-admin)
23. [FR-QTA — Quotas, sampling, spike protection](#23-fr-qta--quotas-sampling-spike-protection)
24. [FR-SEC — Privacy, PII, compliance](#24-fr-sec--privacy-pii-compliance)
25. [FR-API — APIs, CLI, CI/CD](#25-fr-api--apis-cli-cicd)
26. [Layer-by-layer coverage](#26-layer-by-layer-coverage) — frontend, backend, DB, mobile, infra
27. [Non-functional requirements](#27-non-functional-requirements)
28. [Deployment models](#28-deployment-models)
29. [What Sentry is not](#29-what-sentry-is-not)
30. [Glossary](#30-glossary)
31. [Appendix A — worked end-to-end trace](#appendix-a--worked-end-to-end-trace)

---

## 1. What Sentry is

Sentry is an **application-level observability platform** built around a single organising idea: *a human should be handed a debuggable, deduplicated, actionable problem — not a dashboard to interpret*.

Where classic observability tools present three independent pillars (metrics, logs, traces) and leave correlation to the operator, Sentry inverts it:

| Classic APM | Sentry |
|---|---|
| Signal-first: here are metrics, go find the problem | Problem-first: here is Issue #4213, 812 users affected, introduced in release `v2.4.1` by commit `a3f9c` |
| Correlation is the operator's job | Correlation is the storage key: everything hangs off `trace_id`, `issue_id`, `release`, `user_id` |
| Host/infrastructure oriented | Code oriented — stack frame, function, query, commit, author |

**The three primitives:**

1. **Event** — a single thing that happened (an error, a transaction, a check-in). Immutable, timestamped, richly contextual.
2. **Issue** — a *group* of events judged to be the same underlying bug via fingerprinting. This is the unit of workflow: assign, resolve, ignore, regress.
3. **Trace** — a causal tree of spans spanning process boundaries, tying a browser click to a backend handler to a SQL query to a queue worker.

**Everything else is a projection of those three.** Release health is events grouped by release. Web Vitals are span measurements grouped by page. Replay is a recording keyed by the same `trace_id`. Profiling is a stack sampler keyed to the same spans.

### 1.1 Product surface at a glance

| Product | Signal captured | Primary question answered |
|---|---|---|
| Error Monitoring | Exceptions, messages | What broke, where in the code, for whom |
| Tracing / Performance | Transactions & spans | Where did the time go, across which services |
| Insights | Derived span aggregates | Which endpoint / query / cache / queue is the problem |
| Session Replay | DOM mutations + network + console | What did the user actually see and do |
| Profiling | Sampled call stacks | Which *function* burns the CPU/wall time |
| Logs ⚠️ | Structured log records | What was the app saying around the failure |
| Metrics ⚠️ | Counters/gauges/distributions | Business + custom numeric trends |
| Cron Monitors | Job check-ins | Did the scheduled job run, on time, successfully |
| Uptime Monitors | Synthetic HTTP probes | Is the endpoint reachable from outside |
| Release Health | Sessions | Is this release crash-free, is it being adopted |
| User Feedback | Human-submitted reports | What did the user say went wrong |
| Seer / AI | Derived from all of the above | What is the root cause and what's the patch |

---

## 2. Core domain model

### 2.1 Entity hierarchy

```
Organization
 ├── Members (users) ── Roles
 ├── Teams
 └── Projects                         (1 project ≈ 1 deployable / 1 codebase)
      ├── Client Keys (DSN)           (public ingest credential)
      ├── Environments                 production / staging / dev
      ├── Releases  ── Deploys ── Commits ── Authors
      ├── Issues ── Events
      ├── Traces ── Transactions ── Spans
      ├── Replays / Profiles / Attachments
      ├── Monitors (cron) / Uptime checks
      ├── Alert Rules (issue + metric)
      └── Ownership rules / Code mappings
```

**FR-DM-1** — The system SHALL scope all data to an Organization; a Project is the unit of ingest, quota attribution, and access control. A Team grants a set of members access to a set of projects.

**FR-DM-2** — A **DSN** (Data Source Name) SHALL be a public, embeddable credential of the form
`https://<public_key>@<host>/<project_id>`.
It identifies the project and is safe to ship in client bundles: it can only write, never read.

**FR-DM-3** — **Environment** SHALL be a first-class dimension on every event, filterable across every product surface, and settable per-SDK-init or per-scope.

### 2.2 The Event

An event is a JSON document. Canonical fields:

| Field | Meaning |
|---|---|
| `event_id` | 32-char hex UUID, client-generated |
| `timestamp` | When it occurred (client clock, server-corrected) |
| `platform` | `javascript`, `python`, `java`, `cocoa`… drives rendering |
| `level` | fatal / error / warning / info / debug |
| `logger`, `transaction`, `server_name` | Coarse routing/context |
| `exception` | Chained exception values, each with a `stacktrace` of `frames` |
| `stacktrace.frames[]` | `filename`, `function`, `module`, `lineno`, `colno`, `abs_path`, `in_app`, `context_line`, `pre_context`, `post_context`, `vars` |
| `breadcrumbs[]` | Ordered trail of what happened before |
| `tags{}` | Low-cardinality indexed key/values — searchable, aggregatable |
| `contexts{}` | Structured blobs: `os`, `device`, `browser`, `runtime`, `trace`, `gpu`, `app`, `culture` |
| `extra{}` | Arbitrary unindexed JSON |
| `user{}` | `id`, `email`, `username`, `ip_address`, `segment` |
| `request{}` | URL, method, headers, query string, body (scrubbed) |
| `release`, `dist`, `environment` | Version correlation keys |
| `sdk{}` | SDK name, version, enabled integrations |
| `fingerprint[]` | Grouping override |
| `modules{}` | Installed dependency versions |

**FR-DM-4** — Tags SHALL be indexed and low-cardinality (searchable, facetable, alertable). `extra` SHALL be stored but not indexed. The distinction MUST be enforced at ingest so high-cardinality data cannot degrade the search index.

### 2.3 The Trace / Span model

**FR-DM-5** — A **Trace** SHALL be identified by a 16-byte `trace_id` and SHALL span every service, process and thread involved in one logical operation.

**FR-DM-6** — A **Span** SHALL carry:

| Field | Meaning |
|---|---|
| `span_id` (8 bytes), `parent_span_id`, `trace_id` | Tree structure |
| `op` | Category: `http.server`, `http.client`, `db.query`, `cache.get`, `queue.publish`, `ui.render`, `resource.script`, `function`, `gen_ai.chat` |
| `description` | Human/parameterised detail, e.g. `SELECT * FROM users WHERE id = %s` |
| `start_timestamp`, `timestamp` | Wall-clock bounds → duration |
| `status` | `ok`, `cancelled`, `internal_error`, `not_found`, … (gRPC-derived) |
| `data{}` / attributes | `db.system`, `http.response.status_code`, `server.address`, `code.filepath` … (OTel semantic conventions) |
| `measurements{}` | Named numeric values with units — `lcp`, `cls`, `frames_slow`, `app_start_cold` |
| `origin` | Which instrumentation created it (`auto.http.django`, `manual`) |

**FR-DM-7** — A **Transaction** SHALL be the root span of one service's participation in a trace, named by a *route pattern* not a raw URL (`/users/:id`, never `/users/8123`), so that aggregation is meaningful.

⚠️ **FR-DM-8** — The platform has migrated from a transaction-centric model to a **span-first** model: spans are stored, billed and queried as independent first-class rows; a transaction is a span with no local parent. New work SHOULD assume span-first storage.

### 2.4 The Session (release health)

**FR-DM-9** — A **Session** SHALL represent one continuous period of app use, with status `ok` / `errored` / `crashed` / `abnormal`, attributed to a `release` + `environment`, and SHALL be aggregated into crash-free-rate metrics. Sessions are sent as lightweight aggregates, not per-user rows, and are **not** subject to error sampling.

---

## 3. Ingestion & processing pipeline

```
  ┌──────────┐   envelope     ┌────────┐   Kafka    ┌────────────┐
  │   SDK    │ ─────────────► │ Relay  │ ─────────► │ Processing │
  └──────────┘   HTTPS/gzip   └────────┘            └──────┬─────┘
   sampling         │  PII scrub, rate limit,              │
   buffering        │  filters, dynamic sampling,          │ symbolication
   retry/backoff    │  normalisation, quota check          │ grouping
                    │                                      │ enrichment
                    ▼                                      ▼
              (optional on-prem                    ┌──────────────┐
               Relay for PII                       │  ClickHouse  │
               egress control)                     │  via Snuba   │
                                                   └──────┬───────┘
                                                          ▼
                                              Search / Alerts / UI / API
```

**FR-ING-1 (Envelope protocol)** — SDKs SHALL transmit an **envelope**: a newline-delimited container with a header plus N typed items (`event`, `transaction`, `session`, `attachment`, `replay_event`, `replay_recording`, `profile`, `check_in`, `log`, `client_report`). One HTTP request MAY carry multiple item types.

**FR-ING-2 (Relay)** — An edge service SHALL, before any storage:
- authenticate the DSN and enforce project rate limits & quotas;
- apply **inbound filters** (browser extension noise, legacy browsers, web crawlers, localhost, denied release/error-message patterns, IP blocklist);
- apply **server-side PII scrubbing rules**;
- normalise and validate the payload (reject/repair malformed events);
- apply **dynamic sampling** rules;
- buffer and forward to Kafka.

**FR-ING-3** — Relay SHALL be deployable **on the customer's own infrastructure** in front of Sentry SaaS, so that PII can be stripped before leaving the customer network.

**FR-ING-4 (Client reports)** — SDKs SHALL report their own dropped events (reason: sampling, rate limit, queue overflow, network error) so users can distinguish "not happening" from "not delivered".

**FR-ING-5 (Backpressure)** — SDKs SHALL honour `429` + `Retry-After` and `X-Sentry-Rate-Limits` per data category, and MUST NOT block application threads; transport SHALL be async with a bounded queue that drops rather than grows.

**FR-ING-6 (Storage)** — Events SHALL be stored in a columnar OLAP store (ClickHouse) behind a query abstraction (Snuba) supporting sub-second aggregation over billions of rows, with per-category retention (see §27).

---

## 4. FR-ERR — Error monitoring

The founding product.

### 4.1 Capture

**FR-ERR-1** — SDKs SHALL capture **unhandled** errors automatically by hooking the platform's global handlers:

| Platform | Hook |
|---|---|
| Browser JS | `window.onerror`, `window.onunhandledrejection`, wrapped `setTimeout`/event listeners |
| Node | `process.on('uncaughtException')`, `unhandledRejection`, framework error middleware |
| Python | `sys.excepthook`, `threading.excepthook`, WSGI/ASGI middleware, framework signals |
| Java/JVM | `Thread.setDefaultUncaughtExceptionHandler`, servlet filters, Logback/Log4j2 appenders |
| Go | `recover()` in handler wrappers, `errors.Join` chains |
| Cocoa/Android | Signal handlers + `NSUncaughtExceptionHandler` / `Thread.UncaughtExceptionHandler`, native crash via minidump/Breakpad |
| .NET | `AppDomain.UnhandledException`, `TaskScheduler.UnobservedTaskException` |

**FR-ERR-2** — SDKs SHALL expose manual capture: `captureException(e)`, `captureMessage(str, level)`, `captureEvent(evt)`, each returning an `event_id` usable to link user feedback.

**FR-ERR-3** — Errors SHALL be capturable from logging frameworks as a sink (`logging` handler in Python, Logback appender, Serilog sink, Monolog handler) with a configurable level threshold for *breadcrumb* vs *event*.

**FR-ERR-4 (Chained exceptions)** — The full `cause`/`__cause__`/`InnerException` chain SHALL be captured and rendered, oldest-first, with each link's own stack trace.

**FR-ERR-5 (Threads)** — For crashes, all thread stacks SHALL be captured with the crashing thread flagged.

### 4.2 Presentation

**FR-ERR-6** — The Issue detail view SHALL present:
- the **stack trace**, app frames expanded and system frames collapsed by default (`in_app` heuristic), with ±5 lines of source context per frame and local variable values where the runtime allows (Python, PHP, Node with `includeLocalVariables`);
- **breadcrumbs** ordered leading up to the error;
- **tags** with distribution bars (e.g. "browser: Chrome 71%"), each clickable to filter;
- **contexts** (device, OS, runtime, request, trace);
- **event navigation**: first / last / recommended event, permalink per event;
- **"Suspect commit"** — the commit most likely responsible, with author;
- **affected user count**, **event count**, **first seen / last seen**, **release range**.

**FR-ERR-7 (in-app frames)** — Frames SHALL be classified `in_app` vs system by package/path heuristics per platform, overridable via stack-trace rules, because collapsing framework noise is what makes the trace readable.

---

## 5. FR-GRP — Grouping and issue lifecycle

This is the highest-value and most under-appreciated part of the product: turning 4 million events into 30 problems.

### 5.1 Fingerprinting

**FR-GRP-1** — Each event SHALL be reduced to a **fingerprint**, and events sharing a fingerprint SHALL be one Issue.

**FR-GRP-2** — Default grouping strategy, in precedence order:
1. **Explicit `fingerprint`** supplied by the SDK/user (supports `{{ default }}` to extend rather than replace, and templated values like `{{ tags.transaction }}`).
2. **Stack trace** — the sequence of `in_app` frames, normalised: file paths stripped of build hashes and machine-specific prefixes, line numbers **excluded** (so a shifted line doesn't create a new issue), minified names ignored in favour of the symbolicated ones.
3. **Exception type + value**, with the value normalised (numbers, UUIDs, hex, quoted strings replaced by placeholders) so `Timeout after 3021ms` and `Timeout after 4102ms` group together.
4. **Message**, parameterised where a format string is available.

**FR-GRP-3 (Server-side controls)** — Users SHALL be able to tune grouping without redeploying:
- **Fingerprint rules** — `error.type:"ConnectionError" -> connection-error`;
- **Stack trace rules / enhancements** — mark modules `+app` / `-app` / `-group` to include/exclude frames from grouping;
- **grouping config version** pinning, with a preview of "this change would merge these N issues".

**FR-GRP-4 (Manual)** — Users SHALL be able to **merge** issues judged to be the same and **unmerge** them again.

⚠️ **FR-GRP-5 (Similarity)** — The system SHOULD surface *similar issues* using embedding-based similarity over stack traces and messages, offering one-click merge.

### 5.2 Lifecycle

**FR-GRP-6** — An Issue SHALL have status:

| Status | Meaning |
|---|---|
| Unresolved | Open, counts toward alerts |
| Resolved | Fixed. Optionally *resolve in next release* / *in a specific release* / *in the commit that fixes it* |
| Archived / Ignored | Muted — permanently, until a condition (`until it happens 100 more times in 1 hour`, `until it affects 10 more users`, or a time window) |

**FR-GRP-7 (Regression detection)** — If an event arrives for a Resolved issue in a release **later** than the resolving release, the issue SHALL automatically reopen and be flagged **Regression**, triggering alerts. This is the mechanism that makes "resolve" safe.

**FR-GRP-8 (Ownership)** — Issues SHALL be assignable to a user or team. **Ownership rules** SHALL auto-suggest/auto-assign based on path globs (`path:src/billing/* #billing-team`), URL globs, tag matches, or an imported `CODEOWNERS` file.

**FR-GRP-9 (Priority)** — Issues SHALL carry a priority (high/medium/low) derived from volume, user impact, and issue type, and SHALL support an **escalating** state when a previously-archived issue's rate exceeds its historical forecast.

**FR-GRP-10** — Issue-level metadata SHALL include: event count, user count, times seen over 24h/14d sparkline, list of affected releases, first/last seen, assignee, linked external tickets, activity/comment feed.

---

## 6. FR-CTX — Context enrichment

### 6.1 Scope model

**FR-CTX-1** — SDKs SHALL implement a layered **Scope** holding tags, user, contexts, breadcrumbs, level, fingerprint and attachments, merged into every event at capture time. Scope layers:

| Layer | Lifetime |
|---|---|
| Global scope | Process |
| Isolation scope | One request / one task (auto-forked by framework integrations) |
| Current scope | Innermost block, forked by `withScope()` / `pushScope()` |

**FR-CTX-2** — Isolation MUST be correct under concurrency: request A's `user` MUST NOT leak into request B. Implementation SHALL use `AsyncLocalStorage` (Node), `contextvars` (Python), thread-locals + explicit propagation (JVM/Go).

### 6.2 Breadcrumbs

**FR-CTX-3** — SDKs SHALL auto-record breadcrumbs (ring buffer, default ~100):

| Source | Example |
|---|---|
| HTTP | `GET /api/cart → 500 (312ms)` |
| Navigation | `/checkout → /payment` |
| UI | `click on button#submit` (with a DOM path, text redacted per privacy config) |
| Console/logging | `console.error(...)`, `logger.warn(...)` |
| DB / query | statement + duration |
| Lifecycle | app backgrounded, low memory, network offline |
| Custom | `Sentry.addBreadcrumb({...})` |

**FR-CTX-4** — Breadcrumbs SHALL be attached to the event *at capture*, be filterable via `beforeBreadcrumb`, and never be transmitted for events that are dropped.

### 6.3 Other enrichment

**FR-CTX-5** — SDKs SHALL auto-detect device/OS/runtime/browser/screen/network/battery/memory context where the platform exposes it.

**FR-CTX-6** — `beforeSend` / `beforeSendTransaction` / `beforeSendLog` hooks SHALL allow arbitrary last-mile mutation or dropping (return `null`).

**FR-CTX-7 (Attachments)** — Arbitrary files (logs, screenshots, view hierarchies, minidumps, HTTP bodies) SHALL be attachable to events, with size limits and separate quota accounting. Mobile SDKs SHOULD auto-attach a **screenshot** and **view hierarchy** on crash.

**FR-CTX-8 (Feature flags)** ⚠️ — Evaluated feature-flag values SHOULD be captured as an ordered evaluation log on the event, and correlated so that "this issue only affects users with flag X on" is answerable. Integrations exist for LaunchDarkly, Statsig, Unleash, OpenFeature.

---

## 7. FR-SYM — Source maps, debug symbols, symbolication

A minified stack trace is worthless. This subsystem is what makes production traces readable.

**FR-SYM-1 (JavaScript)** — The platform SHALL resolve minified frames to original source via **source maps**, supporting:
- upload at build time (`sentry-cli sourcemaps upload`, webpack/vite/rollup/esbuild/Next.js/Nuxt/Remix plugins);
- **Debug IDs** — a build-injected unique ID present in both bundle and map, making resolution independent of URL, release name, or hosting path (the modern, reliable mechanism);
- legacy resolution by `release` + `~/path` artifact naming;
- fetching publicly hosted maps via `//# sourceMappingURL` (discouraged; leaks source);
- optional **source file upload** so the UI can show surrounding code context.

**FR-SYM-2 (Native)** — For C/C++/Rust/Swift/Objective-C/Go the platform SHALL symbolicate using uploaded debug information files (dSYM, PDB, ELF `.debug`, Breakpad `.sym`, WASM), matched by **Debug ID / build ID**, and SHALL support:
- **debug symbol servers** (Microsoft, Apple, Electron, NVIDIA public servers or a customer's own S3/GCS/HTTP symbol server) so uploads are optional;
- **BCSymbolMaps** for Apple bitcode-obfuscated builds;
- minidump / Apple crash report / Breakpad / Unreal Engine crash ingestion;
- inline function expansion (one machine frame → many source frames).

**FR-SYM-3 (JVM/Android)** — ProGuard/R8 and DexGuard mapping files SHALL be uploadable and applied to deobfuscate class/method names; Android NDK symbols SHALL be supported for native crashes; source bundles SHALL be uploadable for code context.

**FR-SYM-4 (.NET / others)** — Portable PDBs SHALL be supported; Dart/Flutter debug info; PHP/Python/Ruby need no symbolication but SHALL still support source context upload for environments where files aren't readable at runtime.

**FR-SYM-5 (Diagnostics)** — When symbolication fails, the UI SHALL explain *why* — missing debug ID, mismatched checksum, map not found, `abs_path` mismatch — because silent failure here is the single most common source of user frustration.

---

## 8. FR-TRC — Distributed tracing

### 8.1 Trace propagation

**FR-TRC-1** — SDKs SHALL propagate trace context on outgoing requests via headers:
- `sentry-trace: <trace_id>-<span_id>-<sampled>` (Sentry's own, historically first);
- `baggage:` — W3C baggage carrying the **Dynamic Sampling Context** (`sentry-trace_id`, `sentry-public_key`, `sentry-release`, `sentry-environment`, `sentry-transaction`, `sentry-sample_rate`, `sentry-sampled`, `sentry-org_id`);
- ⚠️ W3C `traceparent` SHOULD be emitted/accepted for OpenTelemetry interop.

**FR-TRC-2** — Propagation SHALL be restricted by an allow-list (`tracePropagationTargets`) so trace headers are not leaked to third-party domains (which also breaks their CORS).

**FR-TRC-3 (Browser → backend handoff)** — Server-rendered pages SHALL be able to seed the browser trace via `<meta name="sentry-trace">` and `<meta name="baggage">` tags, so a page load and its originating server render share one trace.

**FR-TRC-4 (Async/queue propagation)** — Trace context SHALL propagate through message queues (Celery, Sidekiq, SQS, Kafka, RabbitMQ, BullMQ) as message headers, so a job executed minutes later joins the trace that enqueued it.

### 8.2 Sampling

**FR-TRC-5** — Tracing SHALL support **head-based sampling** configured by:
- `tracesSampleRate` — fixed fraction;
- `tracesSampler(ctx)` — a function receiving the parent sampling decision, transaction name, request attributes; returning a rate. This enables "sample 100% of /checkout, 0.1% of /health".

**FR-TRC-6** — The sampling decision SHALL be made once at the trace head and propagated, so traces are never partially recorded (no orphan spans).

**FR-TRC-7 (Dynamic sampling)** ⚠️ — Server-side, Relay SHALL be able to re-sample using the Dynamic Sampling Context to bias retention toward: low-volume transactions (so rare endpoints aren't invisible), new releases, traces containing errors, and dev environments — while respecting the org's quota. The UI SHALL show effective sample rates and extrapolate counts accordingly.

### 8.3 Instrumentation

**FR-TRC-8** — SDKs SHALL auto-instrument, without user code, at minimum:

| Category | Auto-instrumented |
|---|---|
| Inbound HTTP | Django, Flask, FastAPI, Rails, Laravel, Express, Koa, NestJS, Spring, ASP.NET, Gin, Phoenix… |
| Outbound HTTP | `fetch`, `XMLHttpRequest`, `requests`, `httpx`, `axios`, `net/http`, `OkHttp`, `HttpClient` |
| DB | See §26.3 |
| Cache | Redis, Memcached |
| Queue | Celery, Sidekiq, RQ, BullMQ, SQS, Kafka |
| GraphQL | Apollo, graphql-js, Strawberry — with operation name and resolver spans |
| Template rendering | Jinja2, Django templates, ERB, Blade |
| gRPC | Client and server interceptors |
| Serverless | Lambda, Cloud Functions, Vercel, Cloudflare Workers handler wrapping |
| AI / LLM | OpenAI, Anthropic, LangChain, LlamaIndex, Vercel AI SDK — token counts, model, cost |
| Frontend | Page load, navigation, resource timing, long tasks, interactions, component render |

**FR-TRC-9 (Manual)** — `Sentry.startSpan({op, name}, cb)` and equivalents SHALL be available in every SDK, with correct auto-parenting to the active span, plus decorators/annotations where idiomatic (`@sentry.trace`, `@SentrySpan`).

**FR-TRC-10 (OpenTelemetry)** — The platform SHALL interoperate with OTel:
- Sentry SDKs SHOULD be able to run *on top of* the OTel SDK, consuming OTel spans and translating semantic conventions;
- a `SentrySpanProcessor` / `SentryPropagator` SHALL be provided for existing OTel setups;
- ⚠️ an OTLP ingest endpoint SHOULD accept traces/logs from non-Sentry instrumentation.

### 8.4 Trace UI

**FR-TRC-11** — The **Trace View** SHALL render the full cross-service tree as a waterfall with: per-span duration bars, service/project colour coding, span op & description, embedded error markers, embedded profile flamechart, links to the replay covering that time window, and "missing instrumentation" gap indicators.

**FR-TRC-12** — Traces SHALL be searchable and aggregatable as a dataset: "show me traces where `span.op:db.query` and `span.duration > 2s` and `user.email:*@enterprise.com`".

**FR-TRC-13 (Span links)** ⚠️ — Non-parent-child causal relationships (batch fan-in, retries, previous trace on a browser navigation) SHOULD be representable as span links.

---

## 9. FR-PERF — Performance issue detection

Sentry's differentiator over generic tracing: **automatic pattern detection over span trees, promoted into Issues** with the same workflow (assign/resolve/alert) as errors.

**FR-PERF-1** — The system SHALL run detectors over ingested traces and create typed Performance Issues when a pattern matches thresholds:

| Detector | Pattern |
|---|---|
| **N+1 Database Queries** | Repeated near-identical DB spans, siblings, in a loop under one parent |
| **N+1 API Calls** | Same as above for HTTP client spans |
| **Consecutive DB Queries** | Independent sequential queries that could be parallelised/batched |
| **Slow DB Query** | Single query exceeding a duration threshold |
| **Uncompressed Asset** | Large asset served without gzip/brotli |
| **Large HTTP Payload** | Response body over threshold |
| **Render-Blocking Asset** | Script/stylesheet delaying FCP/LCP |
| **Large Render-Blocking Image** | LCP image oversized for viewport |
| **File I/O on Main Thread** | Mobile: blocking disk access on UI thread |
| **DB on Main Thread** | Mobile: blocking query on UI thread |
| **Frame Drop / Janky Frames** | Mobile: sustained slow/frozen frames |
| **HTTP Overhead** | Request queuing due to connection limits |
| **Function Regression** | Profiling-derived: a function's aggregate duration stepped up |
| **Endpoint Regression** | A transaction's p95 stepped up at a detectable changepoint |

**FR-PERF-2** — Each Performance Issue SHALL show the offending span group, the parent transaction, the repeat count, the *cumulative* time wasted, an example trace, and — where derivable — the source file/line and the responsible commit.

**FR-PERF-3** — Detector thresholds SHALL be configurable per project, and individual detectors disableable.

**FR-PERF-4 (Regression detection)** — The system SHALL apply changepoint detection to transaction/function duration time series and open an issue on a statistically significant regression, attributing it to a release window.

---

## 10. FR-INS — Insights modules

Pre-built aggregate views over span data. Each answers one recurring question without the user writing a query.

**FR-INS-1 (Requests / HTTP)** — Aggregate outbound HTTP by domain and parameterised path: throughput, p50/p75/p95 duration, error-response rate (3xx/4xx/5xx breakdown), time-spent ranking. Drill-down: which of *my* endpoints call this third party, and what happens when it's slow.

**FR-INS-2 (Database)** — Aggregate DB spans by **parameterised query text**, grouped across all call sites:
- queries per minute, average & p95 duration, **total time spent** (the ranking that actually matters);
- the endpoints that issue each query;
- query source: file + line where the query originated (`code.filepath`, `code.lineno`);
- per-query trend chart with release markers;
- supports SQL, MongoDB, and ORM-generated statements.

**FR-INS-3 (Caches)** — Hit/miss rate, cache-item size, and **the transactions whose latency is most sensitive to cache misses**.

**FR-INS-4 (Queues)** — Per destination/queue: publish rate, process rate, **time in queue** vs **processing time**, failure rate, and the trace linking publisher to consumer.

**FR-INS-5 (Assets)** — JS/CSS/image resources: size (encoded & decoded), load duration, ranked by total time spent; flags for uncompressed and render-blocking assets; page-level attribution.

**FR-INS-6 (Web Vitals)** — Per-page Core Web Vitals with a composed performance score:

| Vital | What |
|---|---|
| **LCP** | Largest Contentful Paint — main content render |
| **CLS** | Cumulative Layout Shift — visual stability |
| **INP** | Interaction to Next Paint — responsiveness (replaced FID) |
| **FCP** | First Contentful Paint |
| **TTFB** | Time to First Byte |

Requirements: p75 aggregation per Google's methodology; a weighted 0–100 score; opportunity ranking ("fixing LCP on /home gains the most score"); the specific DOM element responsible for LCP/CLS; and a link from any bad sample to its Replay and trace.

**FR-INS-7 (Mobile)** — App start (cold/warm, with per-phase spans), screen loads (TTID — time to initial display, TTFD — time to full display), slow/frozen frame counts, and app-size/vitals comparison across releases.

**FR-INS-8 (LLM / AI Agents)** ⚠️ — For AI applications: per-invocation model, prompt & completion token counts, cost, latency, tool calls, agent step trees, and error/refusal rates — modelled as `gen_ai.*` spans.

**FR-INS-9** — Every Insights row SHALL be a filtered view over the same span dataset, so any aggregate is drillable to individual sample traces spanning the full latency distribution (fast/median/slow samples plotted against the trend line).

---

## 11. FR-RPL — Session Replay

**FR-RPL-1** — The browser SDK SHALL record a reconstruction of the user session as **DOM mutations plus input/interaction events** (rrweb-derived), not as video — yielding small payloads and a fully inspectable DOM at any point in time.

**FR-RPL-2 (Sampling modes)** —
- `replaysSessionSampleRate` — record a fraction of all sessions from the start;
- `replaysOnErrorSampleRate` — **buffer** the last ~60s in memory continuously and only upload if an error occurs. This is the default-recommended mode: cost is near zero until something breaks.

**FR-RPL-3 (Privacy — default deny)** — All text nodes and all `<input>` values SHALL be **masked by default**, and all images/media blocked by default. Requirements:
- `mask` / `unmask` / `block` / `unblock` / `ignore` CSS-selector configuration;
- `data-sentry-mask` / `data-sentry-unmask` attribute controls;
- network request/response **bodies and headers are not captured unless explicitly allow-listed** by URL;
- masking happens **client-side before transmission** — unmasked content must never leave the browser.

**FR-RPL-4 (Replay contents)** — The player SHALL show, time-synchronised with the DOM playback:
- console output, network waterfall (with status/duration/size), breadcrumbs, errors (clickable to the Issue), and trace links;
- a DOM inspector at the current playhead;
- **rage clicks**, **dead clicks**, and slow-click detection as first-class searchable signals;
- URL/route timeline and user/device metadata.

**FR-RPL-5** — Replays SHALL be linked bidirectionally: from an Issue → the replays where it occurred; from a trace → the replay covering it; from a replay → every error and transaction inside it.

**FR-RPL-6 (Mobile Replay)** ⚠️ — iOS/Android/React Native SHALL support replay via periodic redacted screenshots + view-hierarchy capture, with the same default-mask privacy posture.

**FR-RPL-7 (Canvas)** ⚠️ — WebGL/canvas content SHOULD be capturable at a configurable fps for apps whose UI is canvas-rendered.

**FR-RPL-8 (Overhead budget)** — The recorder SHALL cap CPU and payload; long sessions SHALL be segmented and uploaded incrementally; recording MUST degrade gracefully rather than harm the host app.

---

## 12. FR-PRF — Profiling

Tracing tells you *which span* was slow. Profiling tells you *which line of which function*.

**FR-PRF-1** — SDKs SHALL sample call stacks at ~100Hz (configurable) using platform-native mechanisms:

| Platform | Mechanism |
|---|---|
| Python | `sys.setprofile`-free sampling thread |
| Node | V8 CPU profiler |
| Browser | JS Self-Profiling API (requires `Document-Policy: js-profiling` header) |
| iOS/macOS | Signal-based stack sampling |
| Android | JVMTI / `Debug` API + native unwinding |
| Ruby | StackProf-equivalent |
| PHP | Excimer |
| .NET / Java | Async-profiler-style sampling |

**FR-PRF-2 (Modes)** — Two modes SHALL be supported:
- **Transaction-based** — profile only while a sampled transaction is active (`profilesSampleRate`);
- **Continuous** ⚠️ — always-on sampling, billed by profile-hour, giving coverage of work that isn't inside a transaction (startup, background jobs, GC).

**FR-PRF-3 (Views)** —
- **Flamegraph** for a single profile, with frames symbolicated and app-vs-system separated;
- **Aggregate flamegraph** across many profiles for a transaction/function, revealing the *typical* cost distribution;
- **Function list** ranked by self time, total time, and sample count;
- **Function regression detection** feeding §9.

**FR-PRF-4** — Profiles SHALL be attached to their transaction and rendered *inline in the trace waterfall*, so a slow span expands directly into the stack that caused it.

**FR-PRF-5** — Profiling MUST be safe in production: bounded overhead (target <5%), no stop-the-world pauses, and disable-on-error.

---

## 13. FR-LOG — Logs

⚠️ *Newer product surface; verify current state.*

**FR-LOG-1** — SDKs SHALL support structured log emission (`Sentry.logger.info/warn/error(...)`) with typed attributes and template-parameterised messages (`logger.warn(fmt\`user ${id} rate limited\`)` preserving `id` as an attribute and the template as the grouping key).

**FR-LOG-2** — Existing logging frameworks SHALL be bridgeable with one line of config: Python `logging`, Winston/Pino/Bunyan, Logback/Log4j2, Serilog, Monolog, Ruby `Logger`, `console` in the browser.

**FR-LOG-3** — Every log record SHALL carry `trace_id`, `span_id`, `release`, `environment`, and the active scope's tags/user, so logs are **automatically correlated** with the trace and issue rather than joined by timestamp guesswork.

**FR-LOG-4** — The Logs UI SHALL provide: full-text + attribute search, a time histogram, live tail, column selection, auto-refresh, and pivots to the parent trace/issue/replay.

**FR-LOG-5** — Logs SHALL be batched and compressed by the SDK, sampled independently of traces (but inheriting the trace sampling decision where configured), scrubbable via `beforeSendLog`, and subject to their own quota and retention.

**FR-LOG-6** — Logs SHALL be usable as an alert source and as a dashboard widget dataset.

---

## 14. FR-MET — Metrics

⚠️ **History matters here.** Sentry shipped a "Custom Metrics" (DDM) beta, then **sunset it in October 2024** on the grounds that span attributes served the same need better. Metrics subsequently returned in a span-attribute-derived form (trace metrics). Treat this section as intent, not as a stable API.

**FR-MET-1 (Derived metrics)** — The platform SHALL compute time series from stored spans/events without any separate metrics instrumentation: throughput, duration percentiles, failure rate, apdex, user misery, counts by any tag. In practice **this covers most needs** — and is the reason standalone custom metrics were deprioritised.

**FR-MET-2 (Span attributes as metrics)** — Any numeric span attribute or `measurement` SHALL be aggregatable (`avg`, `p50/p75/p95/p99`, `sum`, `count`, `min/max`) and groupable by any string attribute — making `startSpan({attributes: {items_in_cart: 12}})` a metric emission.

**FR-MET-3 (Explicit metrics)** ⚠️ — Where offered, counter/gauge/distribution/set emission SHALL support tags, client-side pre-aggregation over a flush window, and code-location attribution.

**FR-MET-4 (Cardinality safety)** — The system MUST protect itself from unbounded tag cardinality, with visible cardinality limits and per-key enforcement rather than silent data loss.

**FR-MET-5** — Metrics SHALL be queryable in Dashboards and usable as alert conditions with the same operators as span aggregates.

---

## 15. FR-UPT — Uptime and Cron monitoring

### 15.1 Cron / scheduled job monitoring

**FR-UPT-1** — A **Monitor** SHALL be definable with: a slug, a schedule (crontab expression or interval), a timezone, a **check-in margin** (grace period for late starts), a **max runtime**, and a failure/recovery threshold.

**FR-UPT-2** — Jobs SHALL report **check-ins**: an `in_progress` at start (carrying a `check_in_id`) and `ok`/`error` at completion with duration. A single-shot heartbeat check-in SHALL also be supported.

**FR-UPT-3** — The platform SHALL detect and alert on: **missed** (no check-in within margin), **timed out** (started, never finished within max runtime), and **failed** (explicit error status) — each creating an Issue with the standard workflow.

**FR-UPT-4** — Monitors SHALL be auto-instrumentable from schedulers: Celery Beat, `cron` wrapping via CLI (`sentry-cli monitors run <slug> -- ./job.sh`), Django-celery-beat, Sidekiq-cron, Quartz, GitHub Actions, and via decorators (`@monitor(monitor_slug=...)`), including **upsert** of the monitor config from code so the schedule lives with the job.

**FR-UPT-5** — A failed job's check-in SHALL link to the error events and trace produced during that run.

### 15.2 Uptime monitoring

**FR-UPT-6** — HTTP(S) endpoints SHALL be probeable on an interval (e.g. 1–60 min) from Sentry-operated regions, with configurable method, headers, body, timeout, and expected status.

**FR-UPT-7** — Uptime failures SHALL create Issues, distinguish failure classes (DNS, TLS, connection, timeout, status mismatch), and — where the target is Sentry-instrumented — **link the failed probe to the trace it generated on the server side**.

**FR-UPT-8** — Uptime checks SHOULD be auto-discoverable from observed outbound hostnames.

---

## 16. FR-REL — Releases, deploys, release health

**FR-REL-1** — Every event SHALL be taggable with a `release` (an opaque version string; convention `package@1.2.3+build`) and a `dist` (build variant), set at SDK init.

**FR-REL-2 (Release creation)** — Releases SHALL be creatable via API/CLI/CI integration, and SHALL record: version, projects, date created/released, artifacts (source maps, symbols), commits, and deploys per environment.

**FR-REL-3 (Commit association)** — By linking a source repository, the platform SHALL associate a commit range with each release (`sentry-cli releases set-commits --auto`), storing per-commit author, message, and changed files.

**FR-REL-4 (Suspect commits)** — Given a stack trace's `in_app` file paths and the release's commit file-change data, the platform SHALL identify the **most likely responsible commit and author**, display it on the Issue, and optionally auto-assign the issue to that author. This requires a **code mapping** from stack-trace paths to repo paths.

**FR-REL-5 (Release health / sessions)** — SDKs SHALL emit session start/end with status, enabling:

| Metric | Definition |
|---|---|
| Crash-free sessions | % sessions ending without a crash |
| Crash-free users | % distinct users experiencing no crash |
| Adoption | Sessions on this release ÷ total, over time |
| Session duration | Distribution |
| New issues in release | Issues whose `first_seen` falls in this release |

**FR-REL-6** — Releases SHALL be comparable side by side, with a regression view: "issues new in `v2.4.1` vs `v2.4.0`", and crash-free-rate deltas.

**FR-REL-7 (Deploys)** — A deploy SHALL record which release went to which environment and when, be shown as a marker on every time-series chart, and optionally trigger email to committing authors.

**FR-REL-8 (Resolve-in-release)** — "Resolved in next release" SHALL be honoured by the regression detector (§5.2), which requires the release ordering to be well-defined by creation time or semver.

**FR-REL-9 (Artifact lifecycle)** — Source maps and debug files SHALL be scoped to a release/debug-ID and be garbage-collectable, with visibility into artifact storage consumption.

---

## 17. FR-ALR — Alerting and notifications

**FR-ALR-1 (Issue alerts)** — Rules of the form *WHEN condition IF filters THEN actions*, evaluated on ingest:

| Conditions | Filters | Actions |
|---|---|---|
| A new issue is created | Issue is older/newer than X | Send to team/member/owner |
| An issue changes state to escalating | Issue has/hasn't been seen in a release | Send to Slack / Teams / Discord channel |
| An issue is seen more than N times in M minutes | Event's `level`/`tag`/`attribute` matches | Page via PagerDuty / Opsgenie |
| An issue affects more than N users in M | Issue is assigned/unassigned | Create Jira / Linear / GitHub / Azure DevOps ticket |
| Issue percent-change vs previous interval | Issue priority equals | Fire a generic webhook / Sentry App |
| A regression occurs | | |

**FR-ALR-2 (Metric alerts)** — Threshold alerts over any aggregate time series (error count, transaction p95, failure rate, apdex, crash-free rate, custom span aggregate, uptime), with warning + critical thresholds, a resolution threshold, a configurable time window, and per-environment/per-query scoping.

**FR-ALR-3 (Anomaly detection)** ⚠️ — Alerts SHALL optionally trigger on statistical deviation from a learned seasonal baseline instead of a fixed threshold, with sensitivity and direction (above/below/both) settings.

**FR-ALR-4 (Noise control)** — The system SHALL provide: rule-level action intervals (don't re-notify within N minutes), digest/batched notifications, per-user notification settings (email/Slack/in-app, per project, per category), mute windows, and **default automatic muting of issues archived until conditions**.

**FR-ALR-5 (Routing)** — Alerts SHALL be routable to the **issue owner** derived from ownership rules, not only to static targets — this is what makes alerting scale across teams.

**FR-ALR-6 (Delivery integrity)** — Alert evaluation MUST be resilient to ingest lag and MUST not double-fire; every rule SHALL have an audit trail of firings.

---

## 18. FR-QRY — Discover, dashboards, query language

**FR-QRY-1 (Search syntax)** — A single search grammar SHALL apply across issues, events, spans, replays, logs and profiles:

```
is:unresolved level:error browser.name:Chrome release:v2.4.1
  span.duration:>500ms  transaction:"/api/checkout"
  user.email:*@acme.com  !url:*localhost*
  timesSeen:>100  firstSeen:-24h  has:custom_tag
  message:"connection reset" OR message:"broken pipe"
```
Requirements: exact/wildcard/negation, numeric and duration comparators, relative & absolute time, `has:`/`!has:`, boolean `AND`/`OR` with parentheses, aggregate filters (`count():>10`), and typeahead over the project's actual tag keys and values.

**FR-QRY-2 (Discover / Trace Explorer)** — An ad-hoc query builder over the event/span datasets producing a table + chart, with: arbitrary column selection, aggregate functions (`count`, `count_unique`, `avg`, `percentile`, `failure_rate`, `apdex`, `epm`), group-by, sort, equations between columns, sampling-aware extrapolation, saved queries, and CSV export.

**FR-QRY-3 (Dashboards)** — Custom dashboards of widgets (line/area/bar/table/big-number), each bound to a dataset (errors, spans, releases, logs, metrics) and its own query, with dashboard-level filters (project/environment/time/release) that cascade, templates, and sharing.

**FR-QRY-4** — Every chart SHALL support drill-through to the underlying event/span list — no dead-end aggregates.

**FR-QRY-5** — Time-series widgets SHALL overlay release markers and, where relevant, alert thresholds.

---

## 19. FR-FBK — User feedback

**FR-FBK-1 (Crash report modal)** — After a captured error, the SDK SHALL be able to present a dialog collecting name, email and description, associating it with the `event_id` so it lands on the Issue.

**FR-FBK-2 (Feedback widget)** — An embeddable, themeable, unprompted feedback button SHALL be available, capturing message + optional screenshot with annotation/redaction, plus the current URL, user, trace and (if recording) replay.

**FR-FBK-3** — Feedback SHALL be a triageable inbox: mark read/resolved, assign, reply by email, route to Slack/Jira, and spam-filter.

**FR-FBK-4** — Feedback SHALL be submittable server-side via API for apps that collect it through their own UI.

---

## 20. FR-AI — Seer and AI features

⚠️ *Evolving rapidly; capabilities and naming are version-sensitive.*

**FR-AI-1 (Issue summary)** — Generate a plain-language summary of what an issue is, what's likely causing it, and how impactful it is, from the stack trace, breadcrumbs, tags and related traces.

**FR-AI-2 (Root cause analysis)** — Analyse the issue together with the linked repository to produce a ranked root-cause hypothesis with the specific code path implicated.

**FR-AI-3 (Autofix)** — Produce a candidate patch, run it against the repo, and open a **pull request** for human review. Requirements: repo integration with write scope, explicit user invocation, and a diff review step — never auto-merge.

**FR-AI-4 (Similarity & triage)** — Use embeddings for similar-issue detection, duplicate suppression, and priority prediction.

**FR-AI-5 (Data controls)** — Organisations MUST be able to opt out of AI processing and of their data being used for model training, with the setting visible and auditable.

---

## 21. FR-INT — Integrations

**FR-INT-1 (Source control)** — GitHub, GitHub Enterprise, GitLab, Bitbucket, Azure DevOps: commit ingestion, suspect commits, **stack-trace-to-source-code linking** (click a frame → open the exact line on the default branch), PR comments announcing issues introduced/resolved, and `CODEOWNERS` import.

**FR-INT-2 (Issue trackers)** — Jira (+ Server/DC), Linear, GitHub Issues, GitLab, Azure Boards, Asana, Clubhouse/Shortcut, Height: create-and-link, bidirectional status sync (resolve in Sentry when the ticket closes), and **auto-create on alert**.

**FR-INT-3 (Notifications/ChatOps)** — Slack, Microsoft Teams, Discord: rich unfurls, and **interactive actions from the message** (resolve, archive, assign) without leaving chat.

**FR-INT-4 (Paging)** — PagerDuty, Opsgenie, Rootly, incident.io — with service/team mapping and severity routing.

**FR-INT-5 (Deploy platforms)** — Vercel, Netlify, Heroku, AWS Lambda, Cloudflare, Bitbucket Pipelines, GitHub Actions: auto-create releases, auto-upload source maps, auto-notify deploys, inject DSN/env.

**FR-INT-6 (Data export)** — Datadog, Splunk, Amazon SQS, Segment, Rockset-style forwarders: stream events out for organisations that centralise elsewhere.

**FR-INT-7 (Custom / Sentry Apps)** — A public integration platform SHALL allow third parties to build installable apps with: OAuth-scoped API access, webhooks on issue/error/comment/alert events, **UI components** (issue-link modal, alert-rule action, stack-trace link), and org-level installation & permission review.

**FR-INT-8 (Generic webhook)** — Any alert SHALL be deliverable as a signed JSON POST to an arbitrary URL.

---

## 22. FR-ORG — Organisation, access control, admin

**FR-ORG-1 (Roles)** — Billing / Member / Manager / Admin / Owner, plus per-team roles, with a documented permission matrix and the ability to restrict project creation and integration installation.

**FR-ORG-2 (SSO)** — SAML 2.0 (Okta, Entra ID/Azure AD, OneLogin, Auth0, JumpCloud, Google Workspace) and OAuth SSO, with **required-SSO** enforcement and just-in-time provisioning.

**FR-ORG-3 (SCIM)** — Automated user and team provisioning/deprovisioning from the IdP.

**FR-ORG-4 (Audit log)** — Every configuration change (member added, role changed, rule edited, key rotated, data scrubbed, project deleted) SHALL be recorded with actor, IP, timestamp, and be exportable.

**FR-ORG-5 (API tokens)** — Org-level auth tokens with granular scopes, expiry, and rotation; per-user tokens; **internal integrations** for machine access.

**FR-ORG-6 (Project settings)** — Per project: DSN keys (multiple, revocable, individually rate-limited), inbound filters, grouping config, ownership rules, data scrubbing rules, allowed domains (Origin/Referer restriction for browser DSNs), security headers reporting, and retention/quota controls.

---

## 23. FR-QTA — Quotas, sampling, spike protection

**FR-QTA-1 (Categories)** — Consumption SHALL be metered per data category, priced independently: **errors**, **spans**, **replays**, **profile hours**, **attachments (GB)**, **cron monitors**, **uptime monitors**, **logs** ⚠️. Reserved volume + on-demand/pay-as-you-go budget.

**FR-QTA-2 (Spend allocation)** — Per-project spend caps SHALL prevent one noisy project from consuming the whole org's budget.

**FR-QTA-3 (Spike protection)** — On abnormal volume (vs a rolling baseline), the system SHALL automatically shed events for the offending project, notify the org, and record what was dropped — so a runaway loop doesn't exhaust a month's quota in an hour.

**FR-QTA-4 (Client-side controls)** — `sampleRate` (errors), `tracesSampleRate`/`tracesSampler`, `profilesSampleRate`, `replays*SampleRate`, `ignoreErrors`, `denyUrls`/`allowUrls`, `maxBreadcrumbs`, `beforeSend` — all evaluated **before** transmission so sampled-out data costs nothing.

**FR-QTA-5 (Server-side controls)** — Inbound filters, dynamic sampling, per-key rate limits, and per-issue "delete and discard" (permanently drop future events for a known-junk issue).

**FR-QTA-6 (Transparency)** — A usage view SHALL show accepted / filtered / rate-limited / dropped-by-client volume per category over time, with the *reason* for each drop.

---

## 24. FR-SEC — Privacy, PII, compliance

**FR-SEC-1 (Client-side scrubbing)** — `sendDefaultPii` SHALL default to **false**, meaning IP addresses, request bodies, cookies and user identifiers are not captured unless explicitly enabled. `beforeSend` SHALL permit arbitrary redaction before transmission.

**FR-SEC-2 (Server-side scrubbing)** — Advanced data-scrubbing rules SHALL support: rule types (mask/remove/hash/replace), value patterns (credit card, IMEI, IBAN, email, IP, MAC, UUID, US SSN, password-like keys, JWT/API-key regex, custom regex), and selectors (`$string`, `extra.**`, `request.headers.Authorization`, `**.password`), applied at org and project level, **before storage**, with a preview/test facility.

**FR-SEC-3** — Default scrubbing SHALL already remove common secret-bearing keys (`password`, `secret`, `passwd`, `api_key`, `apikey`, `auth`, `credentials`, `token`, `session`).

**FR-SEC-4 (Data residency)** — The platform SHALL offer **US and EU** storage regions selected at org creation, with all ingestion, processing and storage confined to the chosen region.

**FR-SEC-5 (Compliance)** — SOC 2 Type II, ISO 27001, HIPAA (with BAA), GDPR/CCPA support including DSAR-driven deletion by `user.id`, and a documented sub-processor list.

**FR-SEC-6 (Retention & deletion)** — Documented retention per category, project/issue deletion with cascading purge, and an org-level account/data deletion path.

**FR-SEC-7 (Ingest hardening)** — Browser DSNs SHALL be restrictable by allowed domains; a security-report endpoint SHALL accept CSP, Expect-CT, and HPKP violation reports.

**FR-SEC-8** — Encryption in transit (TLS 1.2+) and at rest; secrets never rendered in the UI after creation.

---

## 25. FR-API — APIs, CLI, CI/CD

**FR-API-1 (Web API)** — A documented REST API SHALL cover: organizations, projects, teams, members, issues (list/search/update/bulk-mutate/delete), events, releases, deploys, files/artifacts, monitors, alert rules, dashboards, discover queries, and Snuba-backed aggregate endpoints — with pagination (`Link` headers), rate limits, and token scopes.

**FR-API-2 (sentry-cli)** — A single binary SHALL provide: `login`, `releases new/set-commits/finalize/deploys`, `sourcemaps inject/upload/explain`, `debug-files upload/check/bundle-jvm`, `monitors run`, `send-event`, `issues list/resolve`, `projects`, and `info` — usable unauthenticated-free in CI via `SENTRY_AUTH_TOKEN`.

**FR-API-3 (Build plugins)** — First-party bundler plugins (Webpack, Vite, Rollup, esbuild, Next.js, Nuxt, SvelteKit, Remix, Astro, Expo, Gradle, Xcode, Fastlane, Cocoapods, MSBuild) SHALL handle debug-ID injection, artifact upload, release creation and source-map deletion-after-upload with near-zero configuration.

**FR-API-4 (Wizard)** — `npx @sentry/wizard` SHALL bootstrap SDK install, DSN configuration, example error, and CI token setup interactively.

**FR-API-5 (MCP / agent access)** ⚠️ — An MCP server SHOULD expose issues, traces and search to coding agents, so an agent can pull the failing stack trace directly into its context.

---

## 26. Layer-by-layer coverage

### 26.1 Frontend (browser)

**Setup requirement:** one `Sentry.init()` at the entry point + a build plugin for source maps. Everything below must work with no further code.

| Concern | Mechanism | Data produced |
|---|---|---|
| Unhandled errors | `onerror`, `onunhandledrejection` | Error events with symbolicated stacks |
| Framework errors | React error boundary, Vue `errorHandler`, Angular `ErrorHandler`, Svelte/Solid hooks | Errors with component name & props path |
| Minified stacks | Debug-ID source maps | Original file/function/line |
| Page load performance | Navigation Timing + Paint Timing + `PerformanceObserver` | `pageload` transaction with DNS/TLS/TTFB/FCP/LCP spans |
| SPA navigation | History API / router instrumentation | `navigation` transactions per route |
| Resources | Resource Timing | `resource.script`/`.css`/`.img` spans with size & duration |
| API calls | Patched `fetch`/`XHR` | `http.client` spans **carrying trace headers to the backend** |
| Web Vitals | `PerformanceObserver` for LCP/CLS/INP/FCP/TTFB | Measurements on the pageload transaction + attributed DOM element |
| Long tasks / interactions | Long Task API, `event` timing | `ui.long-task`, INP attribution spans |
| User actions | Click/input listeners | Breadcrumbs + rage/dead-click signals |
| Visual evidence | rrweb-style recorder | Session Replay |
| CPU | JS Self-Profiling API | Browser flamegraphs |
| Offline/failed sends | IndexedDB offline transport queue | Events replayed when connectivity returns |

Framework-specific requirements: SSR/RSC support (Next.js App Router, server actions, middleware, edge runtime), hydration-error detection, route parameterisation (`/product/[id]` not `/product/912`), and tunnelling (`tunnel` option) so ad-blockers can't silently drop the SDK.

### 26.2 Backend

| Concern | Mechanism | Data produced |
|---|---|---|
| Request errors | Framework middleware / error handler | Error events with request context, route, user |
| Request tracing | Middleware creates root span, **continues incoming `sentry-trace`** | `http.server` transaction named by route pattern |
| Outbound calls | HTTP client patch | `http.client` spans + propagated headers |
| Background jobs | Celery / Sidekiq / RQ / BullMQ / Hangfire integrations | `queue.process` transactions joined to the publisher's trace |
| Scheduled jobs | Cron monitor check-ins | Missed/failed/timeout issues |
| Serverless | Handler wrapper (Lambda, Cloud Functions, Vercel, Workers) | Transaction per invocation, cold-start flag, timeout warnings flushed **before** the runtime freezes |
| Logging | Framework log handler | Breadcrumbs + error events + structured logs |
| Profiling | Sampling profiler | Flamegraphs attached to transactions |
| Release health | Request-mode sessions | Crash-free rate per release |
| Local variables | Runtime introspection | Frame-local values in the stack trace |

Hard requirements specific to backends:
- **Concurrency isolation** (§6.1) — non-negotiable; scope leakage across requests corrupts every downstream attribution.
- **Serverless flush semantics** — the SDK MUST flush with a deadline before the platform freezes/kills the process, or events are lost exactly when they matter.
- **Route parameterisation** — a transaction named with a raw path destroys aggregation and inflates cardinality.
- **Graceful degradation** — Sentry being unreachable MUST NOT raise into the application.

### 26.3 Database

Sentry does **not** monitor the database server (no host CPU, buffer pool, replication lag — see §29). It monitors *the application's interaction with* the database, which is where most application-visible DB pain originates.

| Concern | Mechanism | Data produced |
|---|---|---|
| Query capture | ORM/driver instrumentation: SQLAlchemy, Django ORM, Psycopg, asyncpg, mysqlclient, ActiveRecord, Sequelize, Prisma, TypeORM, Knex, Mongoose, GORM, JDBC, Dapper/EF Core, PDO, Eloquent | `db.query` spans |
| Statement text | Captured **parameterised** — literals replaced with placeholders | Safe to store & group; PII in literals is not exfiltrated |
| Attributes | `db.system`, `db.name`, `db.operation`, `server.address`, table name | Filterable dimensions |
| Query origin | Stack capture at span start | `code.filepath` + `code.lineno` — "this query comes from `orders/views.py:88`" |
| Aggregation | Insights Database module | Per-query throughput, p95, **total time spent**, trend, calling endpoints |
| N+1 detection | Sibling-span pattern detector | A Performance Issue naming the repeated query and its parent |
| Consecutive queries | Sequential-independent detector | Parallelisation opportunity issue |
| Slow query | Threshold detector | Issue with example trace |
| Connection pool | Where the driver exposes it | Pool wait time as span/measurement |
| Mobile DB | Core Data / Room / SQLite instrumentation | `db` spans + "DB on main thread" issue |
| Cache tier | Redis/Memcached instrumentation | `cache.get/set` spans, hit rate, item size |

**FR-DB-1** — Query text MUST be parameterised before transmission by default; raw literal values MUST NOT be sent unless explicitly opted in.
**FR-DB-2** — Query grouping MUST be stable across differing literals and whitespace, so one logical query is one row in Insights.
**FR-DB-3** — A DB span MUST be attributable to (a) the endpoint that triggered it, (b) the trace, and (c) the source line — all three, or the aggregate is not actionable.

### 26.4 Mobile

| Concern | Data |
|---|---|
| Native crashes | Signal/minidump capture, symbolicated via dSYM/ProGuard/NDK symbols |
| ANRs / App Hangs | Watchdog thread detects main-thread blockage → issue with the blocked stack |
| OOM terminations | Heuristic detection on next launch |
| App start | Cold/warm start transaction with pre-main, runtime-init, UI-init spans |
| Screen load | TTID / TTFD per screen |
| Frames | Slow (>16ms) and frozen (>700ms) frame counts per screen |
| Network | Full trace continuation into the backend |
| Battery/network/device | Contexts |
| Evidence | Screenshot + view hierarchy on crash; mobile replay |
| Release health | Crash-free sessions/users per release — the primary mobile KPI |
| Size | App size tracking across releases ⚠️ |

### 26.5 Infrastructure & edge

Sentry's infra coverage is **request-path only**: Lambda/Cloud Run/Workers invocations, Kubernetes pod metadata as tags via env, and CDN/edge middleware traces. Host-level metrics are explicitly out of scope (§29) — they are expected to come from Prometheus/Datadog/CloudWatch alongside.

---

## 27. Non-functional requirements

**NFR-1 (Ingest scale)** — Accept and process events at multi-million-per-minute rates with horizontal scaling at the Relay and Kafka tiers; ingest MUST degrade by shedding (with client reports), never by blocking.

**NFR-2 (Latency)** — p95 time from event capture to visibility in the UI ≤ 60s; alert evaluation on comparable latency. Symbolication may add bounded delay and MUST NOT block the event's initial availability.

**NFR-3 (Query performance)** — Aggregate queries over 14 days of a large project's spans SHALL return in < 5s; issue search in < 2s.

**NFR-4 (SDK overhead)** — Bundle size budgets for browser SDKs (core error-only well under ~30KB gzipped; tracing/replay as separately tree-shakeable additions); CPU overhead of tracing <2%, profiling <5%, replay bounded and self-limiting.

**NFR-5 (SDK safety)** — An SDK failure MUST NEVER crash, hang, or visibly slow the host application. All capture paths wrapped; network async; bounded memory; no unbounded retry.

**NFR-6 (Retention)** ⚠️ — Typical: errors 90 days, transactions/spans 90 days, replays 90 days, attachments 30 days, profiles shorter. Retention SHALL be documented per category and configurable on enterprise plans.

**NFR-7 (Availability)** — Ingest availability target ≥ 99.9%; ingest MUST remain available even when the query/UI tier is degraded (accepting data matters more than reading it).

**NFR-8 (Compatibility)** — SDKs SHALL support the platform's supported-version window and degrade gracefully on older runtimes; a documented deprecation and major-version migration path SHALL exist.

**NFR-9 (Determinism of grouping)** — A grouping-algorithm change MUST be versioned and opt-in per project; silently re-grouping an org's issue history is unacceptable.

---

## 28. Deployment models

| Model | Notes |
|---|---|
| **SaaS (sentry.io)** | US or EU region; the reference implementation |
| **Self-hosted** | Docker Compose distribution (Relay, web, workers, Kafka, ClickHouse, Postgres, Redis, Symbolicator, Snuba, Vroom). Functional-parity-minus: no billing, some newer/AI features gated. FSL/BSL-style licence — free for internal use, not for reselling as a competing service. |
| **Single tenant** | Dedicated managed instance for compliance/isolation needs |
| **Hybrid (customer Relay)** | Self-hosted Relay in front of SaaS for PII egress control |

**FR-DEP-1** — Self-hosted SHALL be upgradeable via a documented migration path with a supported version skew, and SHALL expose the same API surface so tooling is portable.
**FR-DEP-2** — Data SHALL be exportable (API + CSV) sufficient to avoid lock-in for the org's own events.

---

## 29. What Sentry is not

Stating the boundary is a requirement — it prevents mis-scoping a project that adopts it.

| Not covered | Use instead |
|---|---|
| Host/infra metrics (CPU, memory, disk, network per node) | Prometheus, Datadog, CloudWatch |
| Database server internals (locks, replication lag, buffer cache, `EXPLAIN` plans) | pganalyze, Percona, native DB tooling |
| Log aggregation at SIEM scale / long-retention log archive | Splunk, Elastic, Loki, S3 |
| Network/synthetic monitoring beyond simple HTTP uptime | Catchpoint, Pingdom, ThousandEyes |
| RUM-as-analytics (funnels, cohorts, retention) | Amplitude, PostHog, GA |
| Kubernetes/container orchestration observability | Datadog, Grafana, native |
| Tracing of non-instrumented third-party systems | Only what your code touches is visible |

The mental model: **Sentry owns the code-facing half of observability; something else owns the machine-facing half.** Overlap exists (both will tell you a service got slow); the difference is Sentry tells you *which function, which query, which commit, which user*.

---

## 30. Glossary

| Term | Meaning |
|---|---|
| **DSN** | Public write-only ingest credential identifying a project |
| **Envelope** | Multi-item transport container for SDK→server payloads |
| **Relay** | Edge ingest service: auth, filter, scrub, sample, forward |
| **Snuba** | Query layer over ClickHouse serving all aggregate queries |
| **Symbolicator** | Service that resolves native/JS stack frames to source |
| **Vroom** | Profiling data service |
| **Fingerprint** | The grouping key that turns events into an Issue |
| **Breadcrumb** | A timestamped trail entry recorded before an event |
| **Debug ID** | Build-unique identifier linking a binary/bundle to its symbols/map |
| **DSC** | Dynamic Sampling Context propagated in the `baggage` header |
| **Scope** | Ambient, layered context merged into events at capture |
| **Session** | Unit of app use powering crash-free-rate metrics |
| **Check-in** | A cron monitor's report that a job started/finished |
| **Suspect commit** | The commit most likely to have introduced an issue |
| **in_app** | Frame classification separating your code from framework code |
| **TTID / TTFD** | Time To Initial / Full Display (mobile screen load) |
| **INP** | Interaction to Next Paint — the responsiveness Web Vital |

---

## Appendix A — worked end-to-end trace

A user clicks **Checkout** and sees an error. What the platform must produce:

```
trace_id = 4f2a…c1

[browser]  txn  navigation /checkout                          1,840ms
           ├─ ui.action.click  button#checkout                    2ms
           ├─ http.client POST /api/orders                    1,790ms   ← sentry-trace + baggage sent
           │
           │   [api service]  txn  POST /api/orders            1,780ms
           │   ├─ db.query  SELECT * FROM carts WHERE id = %s      6ms
           │   ├─ cache.get  user:912:prefs                        1ms   (miss)
           │   ├─ db.query  SELECT * FROM items WHERE cart_id = %s
           │   │     ×47 sequential                              940ms   ⚠ N+1 detector fires
           │   ├─ http.client POST payments.example.com          780ms
           │   │     └─ 502  →  status: internal_error
           │   └─ queue.publish  order.confirm                     3ms
           │           │
           │           └─ [worker]  txn  order.confirm  (+4s later, same trace_id)
           │
           └─ ui.render  ErrorBoundary                             8ms
```

From this single trace the platform derives:

| Surface | Output |
|---|---|
| **Issue** | `PaymentGatewayError: 502 from payments.example.com`, grouped across all occurrences, 312 users affected |
| **Performance Issue** | *N+1 Database Queries* on `SELECT * FROM items WHERE cart_id = %s`, 47 repeats, 940ms wasted, originating at `orders/serializers.py:64` |
| **Insights → Database** | That query ranked by total time spent, with `/api/orders` as its top caller |
| **Insights → Requests** | `payments.example.com` failure rate spike |
| **Insights → Web Vitals** | `/checkout` INP degraded |
| **Replay** | The session showing the spinner, the rage clicks, the error toast |
| **Profile** | Flamegraph of the api-service transaction showing serializer time |
| **Logs** | The worker's `order.confirm` logs, auto-joined by `trace_id` |
| **Release health** | Crash-free rate dip beginning at `v2.4.1` |
| **Suspect commit** | `a3f9c` — "add per-item tax lookup" by @dev, touching `orders/serializers.py` |
| **Alert** | Metric alert on `failure_rate()` for `POST /api/orders` → PagerDuty |
| **Seer** | "The tax lookup added in a3f9c queries per item instead of in bulk; batch it with `select_related`." + a PR |

That chain — click → span → query → commit → author → patch — with no manual correlation by the operator, **is** the product.
