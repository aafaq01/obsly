/**
 * The requests and the routes the SDK used to miss.
 *
 * Two gaps, both of which made the SDK look broken rather than incomplete: a page using axios
 * saw an empty waterfall, because axios speaks XHR and only fetch was patched; and a
 * single-page app reported one transaction for the whole visit, so every route after the first
 * was invisible and the one that did arrive had the duration of the session.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { captureException, close, init } from '../src/index.js'
import { instrumentXhr, type Span, TRACE_HEADER } from '../src/tracing.js'

const DSN = 'https://abc123@obsly.example.com/7'

function fetchMock(implementation: () => Promise<Response>) {
  return vi.fn((_input: RequestInfo | URL, _init?: RequestInit) => implementation())
}

/** A stand-in for XMLHttpRequest: jsdom's would try to make a real request. */
class FakeXhr {
  headers: Record<string, string> = {}
  status = 200
  private listeners: Record<string, (() => void)[]> = {}

  open(_method: string, _url: string): void {}
  send(_body?: unknown): void {}

  setRequestHeader(name: string, value: string): void {
    this.headers[name] = value
  }

  addEventListener(type: string, listener: () => void): void {
    ;(this.listeners[type] ??= []).push(listener)
  }

  /** What the browser does when the response — or the failure — arrives. */
  finish(status: number): void {
    this.status = status
    for (const listener of this.listeners['loadend'] ?? []) listener()
  }
}

describe('XHR', () => {
  let restore: () => void = () => {}
  let originalXhr: typeof XMLHttpRequest

  beforeEach(() => {
    originalXhr = globalThis.XMLHttpRequest
    globalThis.XMLHttpRequest = FakeXhr as unknown as typeof XMLHttpRequest
  })

  afterEach(() => {
    restore()
    globalThis.XMLHttpRequest = originalXhr
  })

  function instrument(spans: Span[], shouldTrace: (url: string) => boolean = () => true) {
    restore = instrumentXhr({
      context: () => ({ traceId: 'b'.repeat(32), sampled: true }),
      shouldTrace,
      onSpan: (span) => spans.push(span),
    })
  }

  function request(method: string, url: string): FakeXhr {
    const xhr = new XMLHttpRequest() as unknown as FakeXhr
    ;(xhr as unknown as XMLHttpRequest).open(method, url)
    ;(xhr as unknown as XMLHttpRequest).send()
    return xhr
  }

  it('traces a request axios would make', () => {
    const spans: Span[] = []
    instrument(spans)

    request('POST', '/api/orders').finish(201)

    expect(spans).toHaveLength(1)
    expect(spans[0]!.description).toBe('POST /api/orders')
    expect(spans[0]!.data['http.status_code']).toBe(201)
  })

  it('carries the trace header, so the backend continues the same trace', () => {
    instrument([])

    const xhr = request('GET', '/api/orders')

    expect(xhr.headers[TRACE_HEADER]).toMatch(/^b{32}-[0-9a-f]{16}-1$/)
  })

  it('leaves a cross-origin request alone', () => {
    // Same reasoning as fetch: a header on a third party's endpoint turns a simple request
    // into a preflighted one, and breaking somebody's payment provider to draw a chart is the
    // wrong trade.
    const spans: Span[] = []
    instrument(spans, () => false)

    const xhr = request('GET', 'https://payments.example.com/charge')
    xhr.finish(200)

    expect(xhr.headers[TRACE_HEADER]).toBeUndefined()
    expect(spans).toEqual([])
  })

  it('records a request that never answered', () => {
    // status 0 is what the browser reports for a network failure, a timeout and an abort —
    // the most interesting requests, and otherwise the gap in the waterfall.
    const spans: Span[] = []
    instrument(spans)

    request('GET', '/api/orders').finish(0)

    expect(spans[0]!.status).toBe('internal_error')
    expect(spans[0]!.data['http.status_code']).toBeUndefined()
  })

  it('restores the prototype when torn down', () => {
    // A patch that outlives the SDK is a patch nobody can turn off.
    const open = XMLHttpRequest.prototype.open
    instrument([])
    restore()

    expect(XMLHttpRequest.prototype.open).toBe(open)
  })
})

interface SentItem {
  type: string
  payload: Record<string, unknown>
}

describe('Single-page navigation', () => {
  // Held rather than read back off globalThis: init() patches fetch, so by the time a test
  // looks, the global is the SDK's wrapper and not the mock underneath it.
  let post: ReturnType<typeof fetchMock>

  function sent(): SentItem[] {
    return post.mock.calls.flatMap((call) => {
      const lines = String(call[1]?.body).trim().split('\n')
      const items: SentItem[] = []
      // header, then (item header, payload) pairs.
      for (let index = 1; index < lines.length; index += 2) {
        items.push({
          type: (JSON.parse(lines[index]!) as { type: string }).type,
          payload: JSON.parse(lines[index + 1]!) as Record<string, unknown>,
        })
      }
      return items
    })
  }

  const transactions = () => sent().filter((item) => item.type === 'transaction')
  const traceOf = (item: SentItem) =>
    (item.payload.contexts as Record<string, Record<string, string>>).trace!.trace_id

  beforeEach(() => {
    vi.useFakeTimers()
    post = fetchMock(() => Promise.resolve(new Response('{}')))
    globalThis.fetch = post as unknown as typeof fetch
    history.replaceState({}, '', '/orders')
  })

  afterEach(() => {
    close()
    vi.useRealTimers()
  })

  async function navigate(to: string) {
    history.pushState({}, '', to)
    // The listener runs in a microtask, so location is already updated when it reads it.
    await Promise.resolve()
  }

  it('reports the page load when the reader moves to another route', async () => {
    // Otherwise a single-page app reports one transaction per visit: every route after the
    // first is invisible, and the one that arrives has the duration of the whole session.
    init({ dsn: DSN, tracesSampleRate: 1 })

    await navigate('/orders/42')

    expect(transactions().map((item) => item.payload.op)).toEqual(['pageload'])
    expect(transactions()[0]!.payload.transaction).toBe('/orders')
  })

  it('reports the new route once it stops fetching', async () => {
    // A navigation has no load event. Ending it at the URL change would measure nothing, and
    // ending it when the reader leaves would measure how long they read.
    init({ dsn: DSN, tracesSampleRate: 1 })
    await navigate('/orders/42')

    vi.advanceTimersByTime(1200)

    const navigation = transactions()[1]!
    expect(navigation.payload.op).toBe('navigation')
    expect(navigation.payload.transaction).toBe('/orders/:id')
  })

  it('gives the new route its own trace', async () => {
    // An hour of navigations hanging off one trace id is a waterfall nobody can read.
    init({ dsn: DSN, tracesSampleRate: 1 })
    await navigate('/orders/42')
    vi.advanceTimersByTime(1200)

    expect(traceOf(transactions()[1]!)).not.toBe(traceOf(transactions()[0]!))
  })

  it('does not treat a query string change as a navigation', async () => {
    // A facet that rewrites ?sort=price through pushState has not navigated anywhere, and
    // counting it would turn every click on a filter into its own page view.
    init({ dsn: DSN, tracesSampleRate: 1 })

    await navigate('/orders?sort=price')
    vi.advanceTimersByTime(2000)

    expect(transactions()).toEqual([])
  })

  it('attributes an error to the route the reader is on', async () => {
    // An error on the fourth screen of a session used to carry the page load's trace, which
    // put it in a waterfall with nothing that happened anywhere near it.
    init({ dsn: DSN, tracesSampleRate: 1 })
    await navigate('/orders/42')

    captureException(new Error('late failure'))
    vi.advanceTimersByTime(1200)

    const error = sent().find((item) => item.type === 'event')!
    const navigation = transactions().find((item) => item.payload.op === 'navigation')!
    const pageload = transactions().find((item) => item.payload.op === 'pageload')!
    expect(traceOf(error)).toBe(traceOf(navigation))
    expect(traceOf(error)).not.toBe(traceOf(pageload))
  })

  it('names the route on the error, so the issue says where it happened', async () => {
    init({ dsn: DSN, tracesSampleRate: 1 })
    await navigate('/orders/42')

    captureException(new Error('late failure'))

    const error = sent().find((item) => item.type === 'event')!
    expect((error.payload.tags as Record<string, string>).transaction).toBe('/orders/:id')
  })

  it('can be turned off for a server-rendered site', async () => {
    init({ dsn: DSN, tracesSampleRate: 1, trackRouteChanges: false })

    await navigate('/orders/42')
    vi.advanceTimersByTime(2000)

    expect(transactions()).toEqual([])
  })

  it('never reports the same transaction twice', async () => {
    // A navigation can reach its idle timer, its deadline and a page hide. A browser firing
    // two of them would count one visit as two.
    init({ dsn: DSN, tracesSampleRate: 1 })
    await navigate('/orders/42')

    vi.advanceTimersByTime(1200)
    dispatchEvent(new Event('pagehide'))
    vi.advanceTimersByTime(60_000)

    expect(transactions()).toHaveLength(2)
  })

  it('leaves a page that never navigates exactly as it was', async () => {
    // The common case. Adding route tracking must not change what a plain page reports.
    init({ dsn: DSN, tracesSampleRate: 1 })

    dispatchEvent(new Event('pagehide'))

    expect(transactions()).toHaveLength(1)
    expect(transactions()[0]!.payload.op).toBe('pageload')
  })
})
