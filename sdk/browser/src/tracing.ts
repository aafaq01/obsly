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
export function instrumentFetch(options: {
  traceId: string
  sampled: boolean
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

    // Headers() rather than a plain object: init.headers may be a Headers, an array of pairs,
    // or an object, and assuming one of the three silently drops the caller's own headers.
    const headers = new Headers(
      init?.headers ?? (input instanceof Request ? input.headers : undefined),
    )
    headers.set(TRACE_HEADER, traceHeader(options.traceId, spanId, options.sampled))

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
