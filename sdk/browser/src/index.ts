import { parseDsn, type Dsn } from './dsn'
import { parseStack } from './stacktrace'
import { hex, instrumentFetch, type Span } from './tracing'
import { buildEnvelope, send, type Item } from './transport'
import { collectVitals, type Measurements } from './vitals'

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
}

interface Client {
  dsn: Dsn
  options: Required<Omit<Options, 'dsn'>>
  traceId: string
  spanId: string
  sampled: boolean
  spans: Span[]
  measurements: Measurements
  sent: boolean
  teardown: (() => void)[]
}

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
  }

  client = {
    dsn: parseDsn(options.dsn),
    options: resolved,
    traceId: hex(16),
    spanId: hex(8),
    sampled: Math.random() < resolved.tracesSampleRate,
    spans: [],
    measurements: {},
    sent: false,
    teardown: [],
  }

  const active = client
  active.teardown.push(
    collectVitals((measurements) => {
      active.measurements = measurements
    }),
  )
  active.teardown.push(
    instrumentFetch({
      traceId: active.traceId,
      sampled: active.sampled,
      shouldTrace: resolved.shouldTrace,
      onSpan: (span) => {
        // Bounded. A page that fires a request per keystroke would otherwise grow this array
        // until the tab runs out of memory — the SDK must not be the leak.
        if (active.spans.length < resolved.maxSpans) active.spans.push(span)
      },
    }),
  )

  installErrorHandlers(active)
  installPageloadReporter(active)
}

/** Report an error the page caught itself. */
export function captureException(error: unknown, context: Record<string, unknown> = {}): void {
  if (!client) return

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
        // The two ids that make this error findable from the request it happened in.
        contexts: { trace: { trace_id: client.traceId, span_id: client.spanId } },
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
        tags: { url: location.pathname, ...stringTags(context) },
        extra: context,
      },
    },
  ])
}

export function close(): void {
  if (!client) return
  for (const undo of client.teardown) undo()
  client = null
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
  const report = () => {
    // Once. `pagehide` and `visibilitychange` both fire on a real navigation, and a browser
    // that fires both would otherwise double every page in the aggregate.
    if (active.sent || !active.sampled) return
    active.sent = true

    const startedAt = new Date(performance.timeOrigin).toISOString()

    sendItems(
      active,
      [
        {
          type: 'transaction',
          payload: {
            event_id: hex(16),
            transaction: active.options.transactionName(location.href),
            // The op the vitals aggregate filters on: a backend request has no layout shift,
            // and mixing the two populations produces a number describing neither.
            op: 'pageload',
            start_timestamp: startedAt,
            timestamp: new Date().toISOString(),
            environment: active.options.environment,
            release: active.options.release,
            contexts: {
              trace: {
                trace_id: active.traceId,
                span_id: active.spanId,
                op: 'pageload',
                status: 'ok',
              },
            },
            measurements: active.measurements,
            spans: active.spans,
            request: { url: location.href },
          },
        },
      ],
      { beacon: true },
    )
  }

  // visibilitychange, not unload: on mobile a tab is often frozen without ever firing unload,
  // and those page loads would simply never be measured.
  const onHidden = () => {
    if (document.visibilityState === 'hidden') report()
  }
  addEventListener('visibilitychange', onHidden)
  addEventListener('pagehide', report)
  active.teardown.push(() => {
    removeEventListener('visibilitychange', onHidden)
    removeEventListener('pagehide', report)
  })
}

function sendItems(active: Client, items: Item[], { beacon = false } = {}): void {
  try {
    send(active.dsn, buildEnvelope(hex(16), items), { beacon })
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

export { parseDsn } from './dsn'
export { parseStack } from './stacktrace'
export { TRACE_HEADER } from './tracing'
