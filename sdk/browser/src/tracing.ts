export const TRACE_HEADER = 'obsly-trace'

export interface Span {
  span_id: string
  parent_span_id: string
  op: string
  description: string
  status: string
  start_timestamp: string
  timestamp: string
  duration_ms: number
  data: Record<string, unknown>
}

export function hex(bytes: number): string {
  const values = new Uint8Array(bytes)
  crypto.getRandomValues(values)
  return Array.from(values, (value) => value.toString(16).padStart(2, '0')).join('')
}

/** `<trace_id>-<span_id>-<sampled>` — the format the Python SDK already reads. */
export function traceHeader(traceId: string, spanId: string, sampled: boolean): string {
  return `${traceId}-${spanId}-${sampled ? '1' : '0'}`
}

/**
 * Where the frontend and the backend stop being two separate stories.
 *
 * Every same-origin request the page makes gets a span and a trace header. The backend SDK
 * already parses that header and continues the trace, so one waterfall holds the click, the
 * request it caused, and the query that made it slow.
 *
 * Cross-origin requests are left alone by default: adding a header to a third party's endpoint
 * turns a simple request into a preflighted one, and an SDK that breaks somebody's payment
 * provider to draw a nicer chart has made the wrong trade.
 */
export interface TraceContext {
  traceId: string
  sampled: boolean
}

/**
 * Read at request time, not at install time.
 *
 * A single-page app changes trace on every route change, and an instrumentation that captured
 * the id when it was installed would stamp every request for the rest of the session with the
 * first page's trace.
 */
export type ContextSource = () => TraceContext

export function instrumentFetch(options: {
  context: ContextSource
  shouldTrace: (url: string) => boolean
  onSpan: (span: Span) => void
}): () => void {
  const original = globalThis.fetch
  if (typeof original !== 'function') return () => {}

  globalThis.fetch = async function patched(
    input: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<Response> {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    const method = init?.method ?? (input instanceof Request ? input.method : 'GET')

    if (!options.shouldTrace(url)) return original.call(globalThis, input, init)

    const spanId = hex(8)
    const started = performance.now()
    const startedAt = new Date().toISOString()
    const { traceId, sampled } = options.context()

    // Headers() rather than a plain object: init.headers may be a Headers, an array of pairs,
    // or an object, and assuming one of the three silently drops the caller's own headers.
    const headers = new Headers(
      init?.headers ?? (input instanceof Request ? input.headers : undefined),
    )
    headers.set(TRACE_HEADER, traceHeader(traceId, spanId, sampled))

    const finish = (status: string, httpStatus?: number) => {
      const duration = performance.now() - started
      options.onSpan({
        span_id: spanId,
        parent_span_id: '',
        op: 'http.client',
        description: `${method.toUpperCase()} ${url}`,
        status,
        start_timestamp: startedAt,
        timestamp: new Date().toISOString(),
        duration_ms: duration,
        data: httpStatus === undefined ? {} : { 'http.status_code': httpStatus },
      })
    }

    try {
      const response = await original.call(globalThis, input, { ...init, headers })
      finish(response.ok ? 'ok' : 'internal_error', response.status)
      return response
    } catch (cause) {
      // The span is still worth having: a request that never answered is the most interesting
      // kind, and it would otherwise be the one gap in the trace.
      finish('internal_error')
      throw cause
    }
  }

  return () => {
    globalThis.fetch = original
  }
}

interface XhrState {
  spanId: string
  method: string
  url: string
  started: number
  startedAt: string
  traced: boolean
}

const XHR_STATE = Symbol('obsly.xhr')

type TrackedXhr = XMLHttpRequest & { [XHR_STATE]?: XhrState }

/**
 * The other half of the requests a page makes.
 *
 * axios uses XHR in the browser by default, and so does every jQuery-era codebase still in
 * production. Instrumenting only fetch meant those applications saw an empty waterfall and
 * reasonably concluded the SDK was broken.
 *
 * Patching the prototype rather than wrapping individual instances, because the application
 * creates them and hands us no seam.
 */
export function instrumentXhr(options: {
  context: ContextSource
  shouldTrace: (url: string) => boolean
  onSpan: (span: Span) => void
}): () => void {
  const XHR = globalThis.XMLHttpRequest
  if (typeof XHR !== 'function') return () => {}

  const originalOpen = XHR.prototype.open
  const originalSend = XHR.prototype.send

  XHR.prototype.open = function patchedOpen(
    this: TrackedXhr,
    method: string,
    url: string | URL,
    ...rest: unknown[]
  ) {
    const href = typeof url === 'string' ? url : url.href
    this[XHR_STATE] = {
      spanId: hex(8),
      method: String(method || 'GET').toUpperCase(),
      url: href,
      started: 0,
      startedAt: '',
      traced: options.shouldTrace(href),
    }
    return originalOpen.apply(this, [method, url, ...rest] as never)
  } as typeof XHR.prototype.open

  XHR.prototype.send = function patchedSend(this: TrackedXhr, body?: unknown) {
    const state = this[XHR_STATE]

    if (state?.traced) {
      state.started = performance.now()
      state.startedAt = new Date().toISOString()

      const { traceId, sampled } = options.context()
      try {
        this.setRequestHeader(TRACE_HEADER, traceHeader(traceId, state.spanId, sampled))
      } catch {
        // A header set on a request the application already sent, or in a state the browser
        // refuses. Measuring must never be the reason a request fails.
      }

      // loadend, not load: it fires for success, error, timeout and abort alike, so a request
      // that never answered — the most interesting kind — still leaves a span behind.
      this.addEventListener('loadend', () => {
        const status = this.status
        options.onSpan({
          span_id: state.spanId,
          parent_span_id: '',
          op: 'http.client',
          description: `${state.method} ${state.url}`,
          status: status === 0 ? 'internal_error' : status < 400 ? 'ok' : 'internal_error',
          start_timestamp: state.startedAt,
          timestamp: new Date().toISOString(),
          duration_ms: performance.now() - state.started,
          data: status === 0 ? {} : { 'http.status_code': status },
        })
      })
    }

    return originalSend.apply(this, [body] as never)
  } as typeof XHR.prototype.send

  return () => {
    XHR.prototype.open = originalOpen
    XHR.prototype.send = originalSend
  }
}

/**
 * Route changes in a single-page app, which the browser never announces.
 *
 * `popstate` covers Back and Forward. Everything else — every router in every framework —
 * goes through pushState or replaceState, which fire no event at all, so the only way to know
 * the user moved is to watch the two functions.
 */
export function instrumentHistory(onRouteChange: (url: string) => void): () => void {
  const history = globalThis.history as History | undefined
  if (!history || typeof history.pushState !== 'function') return () => {}

  const originalPush = history.pushState.bind(history)
  const originalReplace = history.replaceState.bind(history)

  const announce = () => {
    // A microtask, so location has already been updated when the listener reads it.
    queueMicrotask(() => onRouteChange(location.href))
  }

  history.pushState = function patchedPush(...args: Parameters<History['pushState']>) {
    originalPush(...args)
    announce()
  }
  history.replaceState = function patchedReplace(...args: Parameters<History['replaceState']>) {
    originalReplace(...args)
    announce()
  }
  addEventListener('popstate', announce)

  return () => {
    history.pushState = originalPush
    history.replaceState = originalReplace
    removeEventListener('popstate', announce)
  }
}
