/**
 * Two teams, two projects, one page.
 *
 * A composed frontend is several bundles owned by several teams, and each wants its errors in
 * its own project. Until now the SDK was a module singleton: a second `init()` closed the
 * first, so the second team's errors silently replaced the first team's reporting entirely.
 *
 * The rule these tests hold down: separate projects, one trace. Splitting a single page view
 * into two unrelated waterfalls would reproduce, inside one tab, exactly the problem
 * distributed tracing exists to solve.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { captureException, close, init } from '../src/index.js'
import { TRACE_HEADER } from '../src/tracing.js'

const SHELL_DSN = 'https://shellkey@obsly.example.com/1'
const CHECKOUT_DSN = 'https://checkoutkey@obsly.example.com/2'

function fetchMock(implementation: () => Promise<Response>) {
  return vi.fn((_input: RequestInfo | URL, _init?: RequestInit) => implementation())
}

interface SentItem {
  key: string
  type: string
  payload: Record<string, unknown>
}

describe('Microfrontends', () => {
  let post: ReturnType<typeof fetchMock>

  beforeEach(() => {
    vi.useFakeTimers()
    post = fetchMock(() => Promise.resolve(new Response('{}')))
    globalThis.fetch = post as unknown as typeof fetch
    history.replaceState({}, '', '/shop')
  })

  afterEach(() => {
    close()
    vi.useRealTimers()
  })

  function sent(): SentItem[] {
    return post.mock.calls.flatMap((call) => {
      const key = new Headers(call[1]?.headers).get('X-Obsly-Key') ?? ''
      const lines = String(call[1]?.body).trim().split('\n')
      const items: SentItem[] = []
      for (let index = 1; index < lines.length; index += 2) {
        items.push({
          key,
          type: (JSON.parse(lines[index]!) as { type: string }).type,
          payload: JSON.parse(lines[index + 1]!) as Record<string, unknown>,
        })
      }
      return items
    })
  }

  const transactions = () => sent().filter((item) => item.type === 'transaction')
  const trace = (item: SentItem) =>
    (item.payload.contexts as Record<string, Record<string, string>>).trace!

  it('gives each microfrontend its own project', () => {
    // The whole point of separate DSNs: each team's issue stream is theirs.
    init({ dsn: SHELL_DSN, tracesSampleRate: 1 })
    init({ dsn: CHECKOUT_DSN, tracesSampleRate: 1 })

    dispatchEvent(new Event('pagehide'))

    expect(
      transactions()
        .map((item) => item.key)
        .sort(),
    ).toEqual(['checkoutkey', 'shellkey'])
  })

  it('puts them both on one trace', () => {
    // One page view. Two traces would split it into two waterfalls that never meet, which is
    // the failure this feature exists to remove.
    init({ dsn: SHELL_DSN, tracesSampleRate: 1 })
    init({ dsn: CHECKOUT_DSN, tracesSampleRate: 1 })

    dispatchEvent(new Event('pagehide'))

    const [first, second] = transactions()
    expect(trace(first!).trace_id).toBe(trace(second!).trace_id)
  })

  it('indents the microfrontend under the page that hosts it', () => {
    // So the combined waterfall reads shell first, then what it composed — rather than two
    // rows at the top level with no stated relationship.
    init({ dsn: SHELL_DSN, tracesSampleRate: 1 })
    init({ dsn: CHECKOUT_DSN, tracesSampleRate: 1 })

    dispatchEvent(new Event('pagehide'))

    const shell = transactions().find((item) => item.key === 'shellkey')!
    const checkout = transactions().find((item) => item.key === 'checkoutkey')!
    expect(trace(shell).parent_span_id).toBe('')
    expect(trace(checkout).parent_span_id).toBe(trace(shell).span_id)
  })

  it('gives them different span ids', () => {
    // Two transactions sharing one span id would collapse into one row when the trace is
    // assembled, and the second project's would be the one that disappeared.
    init({ dsn: SHELL_DSN, tracesSampleRate: 1 })
    init({ dsn: CHECKOUT_DSN, tracesSampleRate: 1 })

    dispatchEvent(new Event('pagehide'))

    const [first, second] = transactions()
    expect(trace(first!).span_id).not.toBe(trace(second!).span_id)
  })

  it('reports the network spans once, under the page', () => {
    // Nothing about a fetch says which bundle called it, so attributing it to both would
    // double every request in the aggregate.
    init({ dsn: SHELL_DSN, tracesSampleRate: 1 })
    init({ dsn: CHECKOUT_DSN, tracesSampleRate: 1 })

    dispatchEvent(new Event('pagehide'))

    const counts = transactions().map((item) => (item.payload.spans as unknown[]).length)
    expect(counts.filter((count) => count > 0).length).toBeLessThanOrEqual(1)
  })

  it('sends an error to the client that reported it', () => {
    const shell = init({ dsn: SHELL_DSN, tracesSampleRate: 1 })
    const checkout = init({ dsn: CHECKOUT_DSN, tracesSampleRate: 1 })

    checkout.captureException(new Error('card declined'))
    shell.captureException(new Error('nav failed'))

    const errors = sent().filter((item) => item.type === 'event')
    expect(errors.map((item) => item.key)).toEqual(['checkoutkey', 'shellkey'])
  })

  it('keeps each client’s flags to itself', () => {
    // A flag is a decision one team made. Leaking it into another team's events would make
    // the suspect ranking implicate a flag that project never evaluated.
    const shell = init({ dsn: SHELL_DSN, tracesSampleRate: 1 })
    const checkout = init({ dsn: CHECKOUT_DSN, tracesSampleRate: 1 })

    checkout.setFlag('new-payment-form', true)
    checkout.captureException(new Error('boom'))
    shell.captureException(new Error('boom'))

    const errors = sent().filter((item) => item.type === 'event')
    expect(errors[0]!.payload.flags).toEqual({ 'new-payment-form': true })
    expect(errors[1]!.payload.flags).toEqual({})
  })

  it('attributes an uncaught error to the page it happened on', () => {
    // window.onerror carries nothing that says which bundle threw. Guessing would be worse
    // than a stated rule, so the page's own client takes it.
    init({ dsn: SHELL_DSN, tracesSampleRate: 1 })
    init({ dsn: CHECKOUT_DSN, tracesSampleRate: 1 })

    captureException(new Error('uncaught'))

    const errors = sent().filter((item) => item.type === 'event')
    expect(errors.map((item) => item.key)).toEqual(['shellkey'])
  })

  it('keeps both on the same trace across a route change', async () => {
    init({ dsn: SHELL_DSN, tracesSampleRate: 1 })
    init({ dsn: CHECKOUT_DSN, tracesSampleRate: 1 })

    history.pushState({}, '', '/shop/checkout')
    await Promise.resolve()
    vi.advanceTimersByTime(1200)

    const navigations = transactions().filter((item) => item.payload.op === 'navigation')
    expect(navigations).toHaveLength(2)
    expect(trace(navigations[0]!).trace_id).toBe(trace(navigations[1]!).trace_id)
  })

  it('closing one leaves the other reporting', () => {
    init({ dsn: SHELL_DSN, tracesSampleRate: 1 })
    const checkout = init({ dsn: CHECKOUT_DSN, tracesSampleRate: 1 })

    checkout.close()
    dispatchEvent(new Event('pagehide'))

    expect(transactions().map((item) => item.key)).toEqual(['shellkey'])
  })

  it('a single client behaves exactly as it always did', () => {
    // The common case, and none of this may change it.
    init({ dsn: SHELL_DSN, tracesSampleRate: 1 })

    dispatchEvent(new Event('pagehide'))

    expect(transactions()).toHaveLength(1)
    expect(transactions()[0]!.payload.op).toBe('pageload')
    expect(trace(transactions()[0]!).parent_span_id).toBe('')
  })
})

describe('Trace propagation targets', () => {
  let post: ReturnType<typeof fetchMock>

  beforeEach(() => {
    post = fetchMock(() => Promise.resolve(new Response('{}')))
    globalThis.fetch = post as unknown as typeof fetch
  })

  afterEach(() => close())

  function headerOn(index: number): string | null {
    return new Headers(post.mock.calls[index]?.[1]?.headers).get(TRACE_HEADER)
  }

  it('carries the trace to a named origin', async () => {
    // A microservice estate is several origins by definition, and the same-origin default
    // stops the trace at the first hop.
    init({
      dsn: SHELL_DSN,
      tracesSampleRate: 1,
      tracePropagationTargets: ['https://api.example.com'],
    })

    await fetch('https://api.example.com/orders')

    expect(headerOn(0)).toMatch(/^[0-9a-f]{32}-[0-9a-f]{16}-1$/)
  })

  it('still leaves an origin nobody named alone', async () => {
    // Adding a header to a third party's endpoint turns a simple request into a preflighted
    // one. Breaking somebody's payment provider to draw a waterfall is the wrong trade.
    init({
      dsn: SHELL_DSN,
      tracesSampleRate: 1,
      tracePropagationTargets: ['https://api.example.com'],
    })

    await fetch('https://payments.example.com/charge')

    expect(headerOn(0)).toBeNull()
  })

  it('needs no configuration for this page’s own backend', async () => {
    init({ dsn: SHELL_DSN, tracesSampleRate: 1 })

    await fetch('/api/orders')

    expect(headerOn(0)).toBeTruthy()
  })
})
