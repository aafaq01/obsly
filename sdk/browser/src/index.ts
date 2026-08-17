import { parseDsn, type Dsn } from './dsn.js'
import { parseStack } from './stacktrace.js'
import {
  hex,
  instrumentFetch,
  instrumentHistory,
  instrumentXhr,
  type Span,
  type TraceContext,
} from './tracing.js'
import { buildEnvelope, send, type Item } from './transport.js'
import { collectVitals, type Measurements } from './vitals.js'

export interface Options {
  dsn: string
  environment?: string
  release?: string
  /** Fraction of page loads that report a transaction. Errors are never sampled away. */
  tracesSampleRate?: number
  /** Which requests carry a trace header. Same-origin only by default — see tracing.ts. */
  shouldTrace?: (url: string) => boolean
  /**
   * Origins allowed to receive the trace header, beyond this one.
   *
   * A microservice estate is several origins by definition, and the same-origin default stops
   * the trace at the first hop. Each entry matches the start of the request URL, so
   * `https://api.example.com` covers everything under it.
   *
   * Setting this is a decision about the *other* end: the receiving server must allow
   * `obsly-trace` in its CORS `Access-Control-Allow-Headers`, or the browser refuses the
   * request that used to work.
   */
  tracePropagationTargets?: string[]
  /** Turn a URL into the route it belongs to, so `/orders/42` and `/orders/43` aggregate. */
  transactionName?: (url: string) => string
  maxSpans?: number
  /**
   * Report a transaction per route change in a single-page app, not just per page load.
   *
   * On by default. Turn it off for a server-rendered site, where every navigation is a real
   * page load and the history API is never used.
   */
  trackRouteChanges?: boolean
}

type Resolved = Required<Omit<Options, 'dsn'>>

/** What one microfrontend gets back, so it can report to its own project by name. */
export interface ObslyClient {
  captureException(error: unknown, context?: Record<string, unknown>): void
  setFlag(name: string, result: boolean): void
  close(): void
}

interface Client {
  dsn: Dsn
  options: Resolved
  /** Evaluated flags in evaluation order — a log of what had already been decided. */
  flags: Map<string, boolean>
  /** Its own span for the current unit, so each project gets its own transaction row. */
  spanId: string
  sent: boolean
}

/**
 * The unit every client on this page is reporting about: the page load, or one route change.
 *
 * Shared deliberately. Two microfrontends with two projects are still one thing the reader is
 * waiting for, and giving them separate traces would split one page view into two unrelated
 * waterfalls — which is the problem this exists to solve, reproduced inside a single tab.
 */
interface Page {
  op: 'pageload' | 'navigation'
  name: string
  url: string
  traceId: string
  /** The page's own span: what every client's transaction hangs off, and what outbound
   *  requests name as their parent. */
  rootSpanId: string
  sampled: boolean
  /** Network spans. They belong to the page rather than to any one microfrontend, because
   *  nothing about a fetch says which bundle called it. */
  spans: Span[]
  measurements: Measurements
  startedAtMs: number
  startedAt: string
  idleTimer: ReturnType<typeof setTimeout> | null
  deadline: ReturnType<typeof setTimeout> | null
}

const MAX_FLAGS = 100

/**
 * How long a route change waits for the page to go quiet before it is reported.
 *
 * A navigation has no load event to end it. Ending it when the URL changed would measure
 * nothing — the data the new route needs has not been fetched yet — and ending it when the
 * user leaves would measure how long they read the page. Waiting for the requests to stop is
 * the only one of the three that answers "how long until this route was usable".
 */
const IDLE_MS = 1000

/** However busy the page stays, a navigation is not still happening a minute later. */
const MAX_NAVIGATION_MS = 30_000

/**
 * Every client on this page, in the order they initialised.
 *
 * The first one owns the page: its options govern instrumentation, the network spans and the
 * vitals are reported under it, and uncaught errors — which carry nothing that says which
 * bundle they came from — are attributed to it. The rest report their own transactions,
 * their own flags, and the errors they hand over by name.
 */
const clients: Client[] = []
let page: Page | null = null
let teardown: (() => void)[] = []

/**
 * Start reporting.
 *
 * Called once by an ordinary page. Called once per microfrontend by a composed one — each with
 * its own DSN, so each team's errors land in their own project, while the page they share
 * stays one trace.
 */
export function init(options: Options): ObslyClient {
  const resolved: Resolved = {
    environment: options.environment ?? 'production',
    release: options.release ?? '',
    tracesSampleRate: options.tracesSampleRate ?? 1,
    shouldTrace: options.shouldTrace ?? defaultShouldTrace(options.tracePropagationTargets ?? []),
    tracePropagationTargets: options.tracePropagationTargets ?? [],
    transactionName: options.transactionName ?? defaultRoute,
    maxSpans: options.maxSpans ?? 100,
    trackRouteChanges: options.trackRouteChanges ?? true,
  }

  const client: Client = {
    dsn: parseDsn(options.dsn),
    options: resolved,
    flags: new Map(),
    spanId: hex(8),
    sent: false,
  }

  const first = clients.length === 0
  clients.push(client)

  if (first) {
    page = newPage(
      'pageload',
      location.href,
      resolved,
      Math.random() < resolved.tracesSampleRate,
      0,
    )
    client.spanId = page.rootSpanId
    installInstrumentation(resolved)
  }

  return {
    captureException: (error, context) => report(client, error, context ?? {}),
    setFlag: (name, result) => setFlagOn(client, name, result),
    close: () => closeOne(client),
  }
}

/**
 * Record that a feature flag was evaluated, and to what.
 *
 * Called from wherever the decision is made, so the log reflects what the code actually asked
 * for rather than what the flag service says now. Those differ exactly when it matters: during
 * a rollout.
 */
export function setFlag(name: string, result: boolean): void {
  if (clients[0]) setFlagOn(clients[0], name, result)
}

/** Report an error the page caught itself, to the client that owns the page. */
export function captureException(error: unknown, context: Record<string, unknown> = {}): void {
  if (clients[0]) report(clients[0], error, context)
}

/** Stop every client on this page and put the patched globals back. */
export function close(): void {
  if (page) clearTimers(page)
  for (const undo of teardown) undo()
  teardown = []
  clients.length = 0
  page = null
}

function closeOne(client: Client): void {
  const index = clients.indexOf(client)
  if (index === -1) return
  // The last one out turns the instrumentation off. Removing the page's owner while another
  // microfrontend is still reporting would leave that one with no fetch spans.
  if (clients.length === 1) {
    close()
    return
  }
  clients.splice(index, 1)
}

function setFlagOn(client: Client, name: string, result: boolean): void {
  if (!name || typeof result !== 'boolean') return

  // A Map, so re-evaluating moves the flag to the end — the last decision is the one the code
  // acted on. Bounded, because a flag name built from a user id would otherwise grow without
  // limit in a long-lived tab.
  client.flags.delete(name)
  if (client.flags.size >= MAX_FLAGS) {
    // Oldest out: dropping the newest would lose the evaluation closest to the failure.
    client.flags.delete(client.flags.keys().next().value!)
  }
  client.flags.set(name.slice(0, 200), result)
}

function report(client: Client, error: unknown, context: Record<string, unknown>): void {
  if (!page) return

  const value = error instanceof Error ? error : new Error(String(error))
  sendItems(client, [
    {
      type: 'event',
      payload: {
        event_id: hex(16),
        timestamp: new Date().toISOString(),
        level: 'error',
        platform: 'javascript',
        environment: client.options.environment,
        release: client.options.release,
        // The two ids that make this error findable from the request it happened in. Read from
        // the current unit, so an error on the fourth route of a session belongs to that route
        // rather than to the page load an hour ago.
        contexts: { trace: { trace_id: page.traceId, span_id: client.spanId } },
        exception: {
          values: [
            {
              type: value.name || 'Error',
              value: value.message,
              stacktrace: { frames: parseStack(value.stack, location.origin) },
            },
          ],
        },
        request: { url: location.href },
        flags: Object.fromEntries(client.flags),
        tags: { url: location.pathname, transaction: page.name, ...stringTags(context) },
        extra: context,
      },
    },
  ])
}

function newPage(
  op: 'pageload' | 'navigation',
  url: string,
  options: Resolved,
  sampled: boolean,
  startedAtMs: number,
): Page {
  return {
    op,
    name: options.transactionName(url),
    url,
    // A route change starts its own trace. It is a new thing the reader asked for, and hanging
    // an hour of navigations off one trace id would produce a waterfall nobody can read.
    traceId: hex(16),
    rootSpanId: hex(8),
    sampled,
    spans: [],
    measurements: {},
    startedAtMs,
    // Derived from timeOrigin rather than Date.now(): a page open across a clock change would
    // otherwise report a transaction that started after it ended.
    startedAt: new Date(performance.timeOrigin + startedAtMs).toISOString(),
    idleTimer: null,
    deadline: null,
  }
}

function installInstrumentation(options: Resolved): void {
  teardown.push(
    collectVitals((measurements) => {
      // The page load's, always — a route change three screens later has no Largest
      // Contentful Paint, and attaching one there would put a number in a column that means
      // something else.
      if (page?.op === 'pageload') page.measurements = measurements
    }),
  )

  const context = (): TraceContext => ({
    traceId: page?.traceId ?? '',
    // The page's root span, not any client's: it is what a downstream service names as the
    // parent of its own transaction, and it exists whichever microfrontend made the call.
    sampled: page?.sampled ?? false,
  })
  const onSpan = (span: Span) => {
    if (!page) return
    // Bounded. A page that fires a request per keystroke would otherwise grow this array until
    // the tab runs out of memory — the SDK must not be the leak.
    if (page.spans.length < options.maxSpans) page.spans.push(span)
    // Still busy, so the route has not settled yet.
    if (page.op === 'navigation') scheduleIdleFinish(page)
  }

  teardown.push(instrumentFetch({ context, shouldTrace: options.shouldTrace, onSpan }))
  teardown.push(instrumentXhr({ context, shouldTrace: options.shouldTrace, onSpan }))

  if (options.trackRouteChanges) {
    teardown.push(
      instrumentHistory((url) => {
        if (!page || samePath(url, page.url)) return
        startNavigation(url)
      }),
    )
  }

  installErrorHandlers()
  installPageReporter()
}

function startNavigation(url: string): void {
  if (!page) return
  const options = clients[0]?.options
  if (!options) return

  reportPage()
  page = newPage('navigation', url, options, page.sampled, performance.now())

  // Every client gets a fresh span for the new unit; the page's owner keeps the root, so the
  // rest still indent under it.
  for (const [index, client] of clients.entries()) {
    client.spanId = index === 0 ? page.rootSpanId : hex(8)
    client.sent = false
  }

  scheduleIdleFinish(page)
  const started = page
  page.deadline = setTimeout(() => {
    if (page === started) reportPage()
  }, MAX_NAVIGATION_MS)
}

/** Report the route once nothing has been requested for a moment. */
function scheduleIdleFinish(current: Page): void {
  if (current.idleTimer) clearTimeout(current.idleTimer)
  current.idleTimer = setTimeout(() => {
    if (page === current) reportPage()
  }, IDLE_MS)
}

function clearTimers(current: Page): void {
  if (current.idleTimer) clearTimeout(current.idleTimer)
  if (current.deadline) clearTimeout(current.deadline)
  current.idleTimer = null
  current.deadline = null
}

/**
 * One transaction per client, all on the same trace.
 *
 * The page's owner reports the network spans and the vitals; the others report their own row,
 * parented by the owner's span. So a page composed of three microfrontends is three rows in
 * three projects and one waterfall.
 */
function reportPage(): void {
  if (!page) return
  const current = page
  clearTimers(current)
  if (!current.sampled) return

  for (const [index, client] of clients.entries()) {
    // Once. A navigation can reach its idle timer, its deadline and a page hide, and a browser
    // firing two of them would count one visit as two.
    if (client.sent) continue
    client.sent = true

    const owner = index === 0
    sendItems(client, [
      {
        type: 'transaction',
        payload: {
          event_id: hex(16),
          transaction: current.name,
          // The op the vitals aggregate filters on: a backend request has no layout shift, and
          // mixing the two populations produces a number describing neither.
          op: current.op,
          start_timestamp: current.startedAt,
          timestamp: new Date().toISOString(),
          environment: client.options.environment,
          release: client.options.release,
          contexts: {
            trace: {
              trace_id: current.traceId,
              span_id: client.spanId,
              parent_span_id: owner ? '' : current.rootSpanId,
              op: current.op,
              status: 'ok',
            },
          },
          measurements: owner ? current.measurements : {},
          spans: owner ? current.spans : [],
          request: { url: current.url },
        },
      },
    ])
  }
}

function installErrorHandlers(): void {
  const onError = (event: ErrorEvent) => {
    // event.error carries the stack; event.message alone is all a cross-origin script gives,
    // and reporting that as an error with no frames is still better than silence.
    captureException(event.error ?? new Error(event.message))
  }
  const onRejection = (event: PromiseRejectionEvent) => {
    captureException(event.reason)
  }

  addEventListener('error', onError)
  addEventListener('unhandledrejection', onRejection)
  teardown.push(() => {
    removeEventListener('error', onError)
    removeEventListener('unhandledrejection', onRejection)
  })
}

function installPageReporter(): void {
  const onHide = () => reportPage()

  // visibilitychange, not unload: on mobile a tab is often frozen without ever firing unload,
  // and those page loads would simply never be measured.
  const onHidden = () => {
    if (document.visibilityState === 'hidden') onHide()
  }
  addEventListener('visibilitychange', onHidden)
  addEventListener('pagehide', onHide)
  teardown.push(() => {
    removeEventListener('visibilitychange', onHidden)
    removeEventListener('pagehide', onHide)
  })
}

function sendItems(client: Client, items: Item[]): void {
  try {
    send(client.dsn, buildEnvelope(hex(16), items))
  } catch {
    // Reporting must never throw into the host page.
  }
}

/**
 * Which requests carry the trace header.
 *
 * Same origin always, because that is this page's own backend. Anything else only when it was
 * named: adding a header to a third party's endpoint turns a simple request into a preflighted
 * one, and breaking somebody's payment provider to draw a nicer waterfall is the wrong trade.
 */
function defaultShouldTrace(targets: string[]): (url: string) => boolean {
  return (url: string) => {
    try {
      const absolute = new URL(url, location.href)
      if (absolute.origin === location.origin) return true
      return targets.some((target) => absolute.href.startsWith(target))
    } catch {
      return false
    }
  }
}

/**
 * A route change, or the same route with a different query string?
 *
 * Only the path counts. A filter that rewrites `?sort=price` through pushState has not
 * navigated anywhere, and reporting it as one would turn every click on a facet into its own
 * page view.
 */
function samePath(a: string, b: string): boolean {
  try {
    return new URL(a, location.href).pathname === new URL(b, location.href).pathname
  } catch {
    return a === b
  }
}

/**
 * `/orders/42` and `/orders/43` are the same page.
 *
 * Without this every id becomes its own transaction name, and the aggregate is a list of
 * millions of routes seen once each — which is no aggregate at all.
 */
function defaultRoute(url: string): string {
  try {
    const { pathname } = new URL(url, location.href)
    return (
      pathname
        .split('/')
        .map((part) => (/^\d+$/.test(part) || /^[0-9a-f-]{16,}$/i.test(part) ? ':id' : part))
        .join('/') || '/'
    )
  } catch {
    return '/'
  }
}

function stringTags(context: Record<string, unknown>): Record<string, string> {
  const tags: Record<string, string> = {}
  for (const [key, value] of Object.entries(context)) {
    // Tags are indexed and filterable, so only flat scalars belong. An object here would be
    // stringified into something nobody can group by.
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
      tags[key] = String(value)
    }
  }
  return tags
}

export { parseDsn } from './dsn.js'
export { parseStack } from './stacktrace.js'
export { TRACE_HEADER } from './tracing.js'
