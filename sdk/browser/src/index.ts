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

/** One measured unit of what the reader waited for: the page load, or one route change. */
interface Transaction {
  op: 'pageload' | 'navigation'
  name: string
  traceId: string
  spanId: string
  sampled: boolean
  spans: Span[]
  /** Vitals belong to the page load. A route change has no First Contentful Paint. */
  measurements: Measurements
  /** Milliseconds since timeOrigin — 0 for the page load, the clock reading for a route change. */
  startedAtMs: number
  startedAt: string
  url: string
  sent: boolean
  /** Set while waiting for the route to go quiet — see scheduleIdleFinish. */
  idleTimer: ReturnType<typeof setTimeout> | null
  deadline: ReturnType<typeof setTimeout> | null
}

interface Client {
  dsn: Dsn
  options: Required<Omit<Options, 'dsn'>>
  current: Transaction
  /** Evaluated flags in evaluation order — a log of what had already been decided. */
  flags: Map<string, boolean>
  teardown: (() => void)[]
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

let client: Client | null = null

/** Every id in one page load shares this, so an error and its request land in one trace. */
export function init(options: Options): void {
  if (client) close()

  const resolved: Required<Omit<Options, 'dsn'>> = {
    environment: options.environment ?? 'production',
    release: options.release ?? '',
    tracesSampleRate: options.tracesSampleRate ?? 1,
    shouldTrace: options.shouldTrace ?? sameOrigin,
    transactionName: options.transactionName ?? defaultRoute,
    maxSpans: options.maxSpans ?? 100,
    trackRouteChanges: options.trackRouteChanges ?? true,
  }

  const sampled = Math.random() < resolved.tracesSampleRate

  client = {
    dsn: parseDsn(options.dsn),
    options: resolved,
    current: newTransaction('pageload', location.href, resolved, sampled, 0),
    flags: new Map(),
    teardown: [],
  }

  const active = client
  const pageload = active.current

  active.teardown.push(
    collectVitals((measurements) => {
      // The page load's, always — a route change three screens later has no Largest
      // Contentful Paint, and attaching one there would put a number in a column that means
      // something else.
      pageload.measurements = measurements
    }),
  )

  const context = (): TraceContext => ({
    traceId: active.current.traceId,
    sampled: active.current.sampled,
  })
  const onSpan = (span: Span) => {
    const transaction = active.current
    // Bounded. A page that fires a request per keystroke would otherwise grow this array
    // until the tab runs out of memory — the SDK must not be the leak.
    if (transaction.spans.length < resolved.maxSpans) transaction.spans.push(span)
    // Still busy, so the route has not settled yet.
    if (transaction.op === 'navigation') scheduleIdleFinish(active, transaction)
  }

  active.teardown.push(instrumentFetch({ context, shouldTrace: resolved.shouldTrace, onSpan }))
  active.teardown.push(instrumentXhr({ context, shouldTrace: resolved.shouldTrace, onSpan }))

  if (resolved.trackRouteChanges) {
    active.teardown.push(
      instrumentHistory((url) => {
        if (samePath(url, active.current.url)) return
        startNavigation(active, url)
      }),
    )
  }

  installErrorHandlers(active)
  installPageloadReporter(active)
}

/**
 * Record that a feature flag was evaluated, and to what.
 *
 * Called from wherever the decision is made, so the log reflects what the code actually asked
 * for rather than what the flag service says now. Those differ exactly when it matters: during
 * a rollout.
 *
 * A Map, so re-evaluating moves the flag to the end — the last decision is the one the code
 * acted on. Bounded, because a flag name built from a user id would otherwise grow without
 * limit in a long-lived tab.
 */
export function setFlag(name: string, result: boolean): void {
  if (!client || !name || typeof result !== 'boolean') return

  client.flags.delete(name)
  if (client.flags.size >= MAX_FLAGS) {
    // Oldest out: dropping the newest would lose the evaluation closest to the failure.
    client.flags.delete(client.flags.keys().next().value!)
  }
  client.flags.set(name.slice(0, 200), result)
}

/** Report an error the page caught itself. */
export function captureException(error: unknown, context: Record<string, unknown> = {}): void {
  if (!client) return

  const value = error instanceof Error ? error : new Error(String(error))
  const transaction = client.current
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
        // The two ids that make this error findable from the request it happened in. Read
        // from the current transaction, so an error on the fourth route of a session belongs
        // to that route rather than to the page load an hour ago.
        contexts: { trace: { trace_id: transaction.traceId, span_id: transaction.spanId } },
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
        tags: { url: location.pathname, transaction: transaction.name, ...stringTags(context) },
        extra: context,
      },
    },
  ])
}

export function close(): void {
  if (!client) return
  clearTimers(client.current)
  for (const undo of client.teardown) undo()
  client = null
}

function newTransaction(
  op: 'pageload' | 'navigation',
  url: string,
  options: Required<Omit<Options, 'dsn'>>,
  sampled: boolean,
  startedAtMs: number,
): Transaction {
  return {
    op,
    name: options.transactionName(url),
    // A route change starts its own trace. It is a new thing the reader asked for, and hanging
    // an hour of navigations off one trace id would produce a waterfall nobody can read.
    traceId: hex(16),
    spanId: hex(8),
    sampled,
    spans: [],
    measurements: {},
    startedAtMs,
    // Derived from timeOrigin rather than Date.now(): a page open across a clock change would
    // otherwise report a transaction that started after it ended.
    startedAt: new Date(performance.timeOrigin + startedAtMs).toISOString(),
    url,
    sent: false,
    idleTimer: null,
    deadline: null,
  }
}

function startNavigation(active: Client, url: string): void {
  report(active, active.current)

  const next = newTransaction(
    'navigation',
    url,
    active.options,
    active.current.sampled,
    performance.now(),
  )
  active.current = next
  scheduleIdleFinish(active, next)
  next.deadline = setTimeout(() => report(active, next), MAX_NAVIGATION_MS)
}

/** Report the route once nothing has been requested for a moment. */
function scheduleIdleFinish(active: Client, transaction: Transaction): void {
  if (transaction.idleTimer) clearTimeout(transaction.idleTimer)
  transaction.idleTimer = setTimeout(() => report(active, transaction), IDLE_MS)
}

function clearTimers(transaction: Transaction): void {
  if (transaction.idleTimer) clearTimeout(transaction.idleTimer)
  if (transaction.deadline) clearTimeout(transaction.deadline)
  transaction.idleTimer = null
  transaction.deadline = null
}

function report(active: Client, transaction: Transaction): void {
  // Once. A navigation can reach its idle timer and its deadline and a page hide, and a
  // browser that fires two of them would otherwise count one visit twice.
  if (transaction.sent || !transaction.sampled) {
    transaction.sent = true
    clearTimers(transaction)
    return
  }
  transaction.sent = true
  clearTimers(transaction)

  sendItems(active, [
    {
      type: 'transaction',
      payload: {
        event_id: hex(16),
        transaction: transaction.name,
        // The op the vitals aggregate filters on: a backend request has no layout shift, and
        // mixing the two populations produces a number describing neither.
        op: transaction.op,
        start_timestamp: transaction.startedAt,
        timestamp: new Date().toISOString(),
        environment: active.options.environment,
        release: active.options.release,
        contexts: {
          trace: {
            trace_id: transaction.traceId,
            span_id: transaction.spanId,
            op: transaction.op,
            status: 'ok',
          },
        },
        measurements: transaction.measurements,
        spans: transaction.spans,
        request: { url: transaction.url },
      },
    },
  ])
}

function installErrorHandlers(active: Client): void {
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
  active.teardown.push(() => {
    removeEventListener('error', onError)
    removeEventListener('unhandledrejection', onRejection)
  })
}

function installPageloadReporter(active: Client): void {
  const onHide = () => report(active, active.current)

  // visibilitychange, not unload: on mobile a tab is often frozen without ever firing unload,
  // and those page loads would simply never be measured.
  const onHidden = () => {
    if (document.visibilityState === 'hidden') onHide()
  }
  addEventListener('visibilitychange', onHidden)
  addEventListener('pagehide', onHide)
  active.teardown.push(() => {
    removeEventListener('visibilitychange', onHidden)
    removeEventListener('pagehide', onHide)
  })
}

function sendItems(active: Client, items: Item[]): void {
  try {
    send(active.dsn, buildEnvelope(hex(16), items))
  } catch {
    // Reporting must never throw into the host page.
  }
}

function sameOrigin(url: string): boolean {
  try {
    return new URL(url, location.href).origin === location.origin
  } catch {
    return false
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
