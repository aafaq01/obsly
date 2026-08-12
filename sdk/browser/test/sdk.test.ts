import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { parseDsn } from '../src/dsn'
import { parseStack } from '../src/stacktrace'
import { instrumentFetch, TRACE_HEADER, traceHeader } from '../src/tracing'
import { buildEnvelope, send } from '../src/transport'

const DSN = 'https://abc123@obsly.example.com/7'

/** A fetch mock that carries fetch's signature, so `.mock.calls[0][1]` is typed rather than
 *  an empty tuple the compiler has to be argued with. */
function fetchMock(implementation: () => Promise<Response>) {
  return vi.fn((_input: RequestInfo | URL, _init?: RequestInit) => implementation())
}

describe('DSN', () => {
  it('reads the same string the Python SDK takes', () => {
    const dsn = parseDsn(DSN)

    expect(dsn.publicKey).toBe('abc123')
    expect(dsn.projectId).toBe('7')
    expect(dsn.envelopeUrl).toBe('https://obsly.example.com/api/7/envelope/')
  })

  it('keeps the key out of the origin', () => {
    // A URL carrying the credential ends up in referrer logs and devtools traces.
    expect(parseDsn(DSN).origin).toBe('https://obsly.example.com')
  })

  it('rejects a DSN with no key rather than posting anonymously', () => {
    expect(() => parseDsn('https://obsly.example.com/7')).toThrow(/public key/)
  })

  it('rejects a DSN with no project', () => {
    expect(() => parseDsn('https://abc123@obsly.example.com/')).toThrow(/project id/)
  })
})

describe('Envelope', () => {
  it('frames each item with its byte length', () => {
    // The parser must not scan for a newline: a payload can legitimately contain one.
    const body = buildEnvelope('e1', [{ type: 'event', payload: { message: 'a\nb' } }])
    const [, header, payload] = body.split('\n')

    expect(JSON.parse(header!).length).toBe(new Blob([payload!]).size)
  })

  it('measures length in bytes, not characters', () => {
    // A multi-byte character would otherwise make the declared length short, and the server
    // would slice the payload mid-object.
    const body = buildEnvelope('e1', [{ type: 'event', payload: { message: '€' } }])
    const [, header, payload] = body.split('\n')

    expect(JSON.parse(header!).length).toBe(new TextEncoder().encode(payload!).length)
    expect(JSON.parse(header!).length).toBeGreaterThan(payload!.length - 1)
  })
})

describe('Stack traces', () => {
  it('parses the V8 format', () => {
    const frames = parseStack(
      'Error: boom\n    at checkout (https://shop.example.com/app.js:12:5)',
      'https://shop.example.com',
    )

    expect(frames).toHaveLength(1)
    expect(frames[0]).toMatchObject({ function: 'checkout', lineno: 12, in_app: true })
  })

  it('parses the Firefox format', () => {
    const frames = parseStack(
      'checkout@https://shop.example.com/app.js:12:5',
      'https://shop.example.com',
    )

    expect(frames[0]).toMatchObject({ function: 'checkout', lineno: 12 })
  })

  it('marks third-party code as not in_app', () => {
    // A CDN bundle is somebody else's code; marking it in_app buries the frame that matters.
    const frames = parseStack(
      '    at x (https://cdn.other.com/lib.js:1:1)',
      'https://shop.example.com',
    )

    expect(frames[0]!.in_app).toBe(false)
  })

  it('skips lines it cannot parse rather than guessing', () => {
    // A wrong filename sends somebody to the wrong file, which costs more than a missing frame.
    expect(parseStack('Error: boom\n  something unparseable', 'https://x.com')).toEqual([])
  })

  it('orders frames oldest first, matching the server SDK', () => {
    const frames = parseStack(
      [
        'Error: boom',
        '    at inner (https://x.com/a.js:3:1)',
        '    at outer (https://x.com/a.js:9:1)',
      ].join('\n'),
      'https://x.com',
    )

    expect(frames.map((frame) => frame.function)).toEqual(['outer', 'inner'])
  })

  it('survives an error with no stack at all', () => {
    expect(parseStack(undefined, 'https://x.com')).toEqual([])
  })
})

describe('Trace propagation', () => {
  let restore: () => void

  afterEach(() => restore?.())

  it('sends a header the Python SDK can parse', () => {
    const header = traceHeader('a'.repeat(32), 'b'.repeat(16), true)
    const [traceId, spanId, sampled] = header.split('-')

    // The server rejects anything of the wrong length, so this is the contract.
    expect(traceId).toHaveLength(32)
    expect(spanId).toHaveLength(16)
    expect(sampled).toBe('1')
  })

  it('attaches the trace header to a same-origin request', async () => {
    const original = fetchMock(() => Promise.resolve(new Response('{}', { status: 200 })))
    globalThis.fetch = original as unknown as typeof fetch

    const spans: unknown[] = []
    restore = instrumentFetch({
      traceId: 'a'.repeat(32),
      sampled: true,
      shouldTrace: () => true,
      onSpan: (span) => spans.push(span),
    })

    await fetch('/api/orders')

    const headers = new Headers(original.mock.calls[0]![1]?.headers)
    expect(headers.get(TRACE_HEADER)).toMatch(/^a{32}-[0-9a-f]{16}-1$/)
    expect(spans).toHaveLength(1)
  })

  it('leaves a request alone when it should not be traced', async () => {
    // Adding a header to a third party's endpoint turns a simple request into a preflighted
    // one, and breaking somebody's payment provider to draw a chart is the wrong trade.
    const original = fetchMock(() => Promise.resolve(new Response('{}')))
    globalThis.fetch = original as unknown as typeof fetch

    restore = instrumentFetch({
      traceId: 'a'.repeat(32),
      sampled: true,
      shouldTrace: () => false,
      onSpan: () => {},
    })

    await fetch('https://payments.example.com/charge')

    expect(new Headers(original.mock.calls[0]![1]?.headers).get(TRACE_HEADER)).toBeNull()
  })

  it('keeps the caller’s own headers', async () => {
    // init.headers may be a Headers, an array of pairs, or an object. Assuming one silently
    // drops the other two.
    const original = fetchMock(() => Promise.resolve(new Response('{}')))
    globalThis.fetch = original as unknown as typeof fetch

    restore = instrumentFetch({
      traceId: 'a'.repeat(32),
      sampled: true,
      shouldTrace: () => true,
      onSpan: () => {},
    })

    await fetch('/api/x', { headers: { Authorization: 'Bearer t' } })

    const headers = new Headers(original.mock.calls[0]![1]?.headers)
    expect(headers.get('Authorization')).toBe('Bearer t')
    expect(headers.get(TRACE_HEADER)).toBeTruthy()
  })

  it('records a span for a request that never answered', async () => {
    // The most interesting kind, and otherwise the one gap in the trace.
    globalThis.fetch = fetchMock(() =>
      Promise.reject(new Error('offline')),
    ) as unknown as typeof fetch

    const spans: { status: string }[] = []
    restore = instrumentFetch({
      traceId: 'a'.repeat(32),
      sampled: true,
      shouldTrace: () => true,
      onSpan: (span) => spans.push(span),
    })

    await expect(fetch('/api/x')).rejects.toThrow('offline')
    expect(spans[0]!.status).toBe('internal_error')
  })

  it('restores the original fetch when torn down', async () => {
    const original = globalThis.fetch
    restore = instrumentFetch({
      traceId: 'a'.repeat(32),
      sampled: true,
      shouldTrace: () => true,
      onSpan: () => {},
    })
    restore()

    expect(globalThis.fetch).toBe(original)
  })
})

describe('Transport', () => {
  beforeEach(() => {
    globalThis.fetch = fetchMock(() =>
      Promise.resolve(new Response('{}')),
    ) as unknown as typeof fetch
  })

  it('never sends cookies', () => {
    // Carrying a customer's session cookie to our origin would be a hole in their site.
    send(parseDsn(DSN), 'body')

    const mock = globalThis.fetch as unknown as ReturnType<typeof fetchMock>
    expect(mock.mock.calls[0]![1]?.credentials).toBe('omit')
  })

  it('uses a beacon when the page is going away', () => {
    // pagehide is exactly when the numbers matter and exactly when fetch is cancelled.
    const beacon = vi.fn(() => true)
    Object.defineProperty(navigator, 'sendBeacon', { value: beacon, configurable: true })

    send(parseDsn(DSN), 'body', { beacon: true })

    expect(beacon).toHaveBeenCalled()
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })

  it('falls back to fetch when the beacon is refused', () => {
    // Browsers reject a beacon over their size limit; losing the event silently would be worse.
    Object.defineProperty(navigator, 'sendBeacon', { value: () => false, configurable: true })

    send(parseDsn(DSN), 'body', { beacon: true })

    expect(globalThis.fetch).toHaveBeenCalled()
  })

  it('swallows a transport failure rather than throwing into the page', () => {
    globalThis.fetch = fetchMock(() =>
      Promise.reject(new Error('blocked')),
    ) as unknown as typeof fetch

    expect(() => send(parseDsn(DSN), 'body')).not.toThrow()
  })
})
