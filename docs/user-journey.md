# User journey

What somebody actually does with Obsly, in order, and what the product owes them at each step.
Written because two questions kept coming back — *why does creating a project ask about an
organization instead of a language?* and *does the Python SDK only work with FastAPI?* — and both
turned out to be journey problems rather than feature gaps.

The rule this document is built on: **the product should answer these questions where they are
asked.** A roadmap entry nobody reads is not an answer, and neither is a README.

---

## 0. The person

Not a persona sketch. Three people who do genuinely different things, and the journey has to
hold all three.

| | What they came for | What failure looks like |
|---|---|---|
| **The installer** | Get one service reporting, today | Fifteen minutes in and nothing has arrived, with no way to tell whose fault that is |
| **The on-call** | Find out why the thing that just broke, broke | The error is here but what the user did before it is not |
| **The owner** | Know whether the release made it worse | Numbers exist but nothing says which deploy moved them |

Everything below is the installer's path, because it is the one everybody walks first and the one
where every abandonment happens.

---

## 1. First run — there is nobody yet

`docker compose up`, open `:8081`.

An install with no users shows a **register** form, not a login form nobody can satisfy. The first
account is staff and superuser, because there is nobody to grant it anything and an instance whose
only user cannot reach settings is an instance nobody can administer. The door then closes behind
it: registration returns 403 afterwards unless `OBSLY_ALLOW_SIGNUP` is set deliberately.

> **Why it matters.** The alternative is `docker compose exec … createsuperuser`, which is a bad
> first experience and worse advice to put in a README.

## 2. Create a project — and the question this document exists for

**Why it asks for an organization and not a language.**

*Organization* is asked only when there is more than one. One organization is not a choice, it is
a question with one answer, so the create form no longer shows it — the first project on a fresh
install creates a default organization silently and moves on. It exists in the model because
projects, keys, quotas and eventually teams have to hang off something, and because two teams
sharing an instance need a boundary. It is a container, not a step.

*Language* is not asked at all, and that is the deliberate part. **A project holds an application,
not a runtime.** The React page and the Python service behind it must sit in the same project or
`trace_id` cannot join them — and a page load joined to the query it caused is the entire reason
this product exists. Every other tool asks for a platform here, which is why the create form now
says out loud what it is doing:

> one project takes every tier of one application. Install snippets for Python and the browser
> come next.

The platform is then **observed rather than declared**: `detected_platforms()` reads what has
actually reported. A project that claims to be Python and receives nothing is a lie the UI would
have to keep telling; a project that says *nothing reporting yet* is the truth and is also the
next step.

## 3. Set up — both tiers on one page

`/projects/:id/setup` is numbered, because the sequence carries information: the snippet cannot be
pasted before the DSN exists, and nothing can report before the snippet runs.

1. **Copy the DSN.** Safe to ship in a browser bundle — write access to one project, read access to
   nothing.
2. **Install the SDK.** Two tabs, not a choice between them: two things to install. Each tab states
   what it **fits** and what it does **not fit yet** (see §6) — the second half is the one tools
   normally leave out, and it is why somebody on Flask finds out by installing.
3. **Wait for the first event.** Not a spinner. Nothing arriving can last hours while somebody
   deploys, so it names what it is waiting for and turns itself green when something lands.

The tier lives in the URL (`?tier=frontend`), so a link that offers the browser SDK opens on the
browser tab, and a pasted link reopens where it was.

## 4. Half-instrumented — the state everyone is actually in

Almost nobody installs both SDKs in one sitting. The backend goes in on Tuesday; the browser waits
for a frontend deploy. So **a layer page with no data is not an error and not really empty** — it
is a tier nobody has instrumented, and it offers to instrument itself: Frontend offers the browser
SDK, Backend/Database/Cache offer the Python one.

And it reads what is already reporting. With the backend in, the Frontend page does not show a
generic empty state:

> The other half of this application is already reporting, so once this one is in, a page load and
> the request it makes would join into one trace.

That is the reason to add it, it is only true once the first half exists, and it works in both
directions.

## 5. Data lands — where they go next

| They ask | Where |
|---|---|
| What is broken? | **Issues** — grouped by fingerprint, with the stack trace and the trace that produced it |
| Is it slow? | **Insights → Backend** (p50/p75/p95/p99 per endpoint), **Database** (slow queries, N+1) |
| Is the page slow? | **Insights → Frontend** — Core Web Vitals at p75, with the distribution behind the score |
| What ran? | **Traces** — the waterfall from paint to query, repeated spans grouped |
| Did the deploy do it? | **Releases** — health, adoption, and which version introduced an issue |
| Which flag? | The suspect ranking on the issue — flag rate inside the issue against the baseline |
| Tell me without looking | **Alerts** — new issue, regression, frequency |

---

## 6. Framework coverage, stated honestly

The second question this document exists for. **No, the Python SDK is not FastAPI-only** — and it
is also not everything.

### Python — `obsly` on PyPI

| | |
|---|---|
| **Works anywhere Python does** | `obsly.init()`, `capture_exception()`, `capture_message()`, `set_flag()`, manual `start_transaction()` / `start_span()`, and the `logging` handler. Scripts, workers, Celery tasks, notebooks. |
| **Automatic request tracing** | Any **ASGI** app, via `ObslyMiddleware` — it is a plain ASGI wrapper, so FastAPI, Starlette, Litestar, Quart and Django-under-ASGI all work. `obsly.integrations.fastapi` re-exports it because that is where a FastAPI user looks; FastAPI *is* Starlette, so there is nothing FastAPI-specific in it. |
| **Automatic query spans** | SQLAlchemy, via its event hooks. |
| **Works, but coarsely** | Transaction *names* come from `scope["route"]`, which only Starlette and FastAPI set. Elsewhere the name falls back to the raw path, so `/orders/41` and `/orders/42` become separate rows instead of one `/orders/{id}`. Tracing is correct; the grouping is not. |
| **Not yet** | **WSGI** — Flask, and Django under WSGI. Nothing structural blocks it; the middleware simply has not been written. Those apps can still report errors and logs, just not traced requests. |
| **Also not yet** | Redis / cache client instrumentation (the Cache page is fed by manually-named spans today), and outbound `requests` / `httpx` spans. |

### Browser — `obsly-browser` on npm

| | |
|---|---|
| **Framework** | None required. It hooks the browser, not a framework — React, Vue, Svelte, or a plain HTML page are all the same `init()` call. There is no `obsly-react` and there does not need to be. |
| **Collects** | `window.onerror` and `unhandledrejection`, Core Web Vitals (LCP, CLS, INP, FCP, TTFB) via `PerformanceObserver`, and `fetch()` spans. Reported on `visibilitychange` / `pagehide`. |
| **Not yet** | **XHR is not patched**, so axios in its default browser transport is untraced. A single-page app reports **one transaction per page load**, not one per route change. No React error boundary helper. No source maps, so frames are minified. |

### Node.js

**There is no server-side Node SDK.** `obsly-browser` is browser-only — it reaches for `window`,
`document` and `PerformanceObserver`. A Node service can still report by POSTing the envelope
format directly, but that is a protocol, not an SDK.

---

## 7. What the journey says to build next

Each of these is a step above where somebody hits a wall, not a feature from a list.

| Gap | Step it blocks | Branch |
|---|---|---|
| WSGI middleware; XHR spans; SPA route transactions; a Node SDK | §3 — "install the SDK" ends here for a Flask or Express shop | `feat/sdk-frameworks` |
| Source maps | §5 — a browser issue names `a.js:1:48291`, which is no line anyone can open | `feat/source-maps` |
| Breadcrumbs | §5, on-call — the error says what broke, nothing says what the user did to get there | `feat/breadcrumbs` |
| User context | §5, owner — issue counts are event counts, so one user retrying forty times reads as forty people | `feat/user-context` |
| Teams and roles | §2 — the organization is a container with no meaning until membership hangs off it | `feat/teams` |
| Quotas | §5 — one runaway loop fills the database, and now fires an alert per event | `feat/quotas` |

Ordering lives in [roadmap.md](roadmap.md). This document only says which step each gap belongs to,
because a gap without a step is a preference.
