# obsly-browser

The browser half of Obsly. Errors, Core Web Vitals, and traces that join up with the backend.

```bash
npm install obsly-browser
```

```ts
import { init } from 'obsly-browser'

init({
  dsn: 'https://<public key>@localhost:8081/1',
  environment: 'production',
  release: 'web@2026.08.12',
})
```

That is the whole setup. From then on:

- **Uncaught errors and unhandled rejections** are captured with parsed stack frames.
- **Core Web Vitals** (LCP, INP, CLS, FCP, TTFB) are measured from the browser's own timeline
  and sent with the page-load transaction.
- **Same-origin `fetch` and `XMLHttpRequest` calls** get a span and an `obsly-trace` header, so
  the backend SDK continues the same trace. One waterfall holds the page load, the request it
  made, and the query that made it slow. XHR is instrumented because axios uses it in the
  browser by default, and a page whose whole HTTP layer is invisible looks like a broken SDK.
- **Route changes in a single-page app** report their own `navigation` transaction, with their
  own trace.

## Why it is shaped this way

**No runtime dependencies, and CI fails if one appears.** This ships inside somebody's page.
It must not be able to break their build through a version conflict, and it must not add a byte
they did not ask for.

**It never throws into your page.** Every observer, every parse and every send is wrapped. A
reporting tool that takes down the thing it observes has done more harm than the bug it was
trying to report.

**Third-party requests are left alone by default.** Adding a header to another origin's
endpoint turns a simple request into a preflighted one. Breaking somebody's payment provider to
draw a nicer waterfall is the wrong trade — pass `shouldTrace` if you own both ends.

**Cookies are never sent.** The DSN public key is the credential and is designed to be public.
Carrying a customer's session cookie to our origin would be a hole in their site.

**A route change ends when the route goes quiet.** A navigation has no load event. Ending it
when the URL changed would measure nothing — the data the new screen needs has not been fetched
yet — and ending it when the reader leaves would measure how long they read the page. So it ends
after one second with no new request, capped at thirty. A query-string change is not a
navigation: a facet that rewrites `?sort=price` has not gone anywhere, and counting it would turn
every click on a filter into its own page view.

**Vitals belong to the page load, and only to it.** A route change three screens later has no
Largest Contentful Paint, and attaching one there would put a number in a column that means
something else.

**Vitals are reported on `visibilitychange`, not `unload`.** On mobile a tab is frequently
frozen without ever firing `unload`, and those page loads would simply never be measured.

**The send is `fetch` with `keepalive`, never `sendBeacon`.** A beacon is the obvious choice and
the wrong one: it always sends with credentials mode `include`, and CORS forbids a wildcard
`Access-Control-Allow-Origin` for a credentialed request — so every cross-origin beacon is
rejected at the preflight, which is every real deployment. It also returns `true` as soon as the
request is queued, so the failure is unobservable and no fallback behind that return value can
run. `keepalive` outlives the document the same way, takes headers, and lets credentials be
turned off.

## Options

| Option              | Default                 | What it does                                                                                                                                                     |
| ------------------- | ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `dsn`               | —                       | Required. Same string the Python SDK takes.                                                                                                                      |
| `environment`       | `production`            | Tag on every event.                                                                                                                                              |
| `release`           | `''`                    | Tag on every event, for release health.                                                                                                                          |
| `tracesSampleRate`  | `1`                     | Fraction of page loads that report a transaction. Errors are never sampled away.                                                                                 |
| `shouldTrace`       | same-origin             | Which requests carry a trace header.                                                                                                                             |
| `transactionName`   | route with ids replaced | `/orders/42` and `/orders/43` are the same page. Without this, every id becomes its own transaction name and the aggregate is millions of routes seen once each. |
| `maxSpans`          | `100`                   | A page firing a request per keystroke must not grow this array until the tab runs out of memory.                                                                 |
| `trackRouteChanges` | `true`                  | Report a transaction per route change. Turn it off on a server-rendered site, where every navigation is a real page load and the history API is never used.      |

## Reporting an error yourself

```ts
import { captureException } from 'obsly-browser'

try {
  await checkout()
} catch (error) {
  captureException(error, { cartId, step: 'payment' })
  throw error
}
```

Scalar context values also become filterable tags. Objects stay in `extra`, because a tag you
cannot group by is not a tag.

## Feature flags

```ts
import { setFlag } from 'obsly-browser'

const enabled = flags.isEnabled('new-checkout')
setFlag('new-checkout', enabled)
```

Errors and page loads sent afterwards carry the evaluation log, in evaluation order. The issue
page then ranks flags by how much more often each was on inside that issue than elsewhere, which
is what turns a list of flags into a suspect.
