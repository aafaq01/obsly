import type { Dsn } from './dsn.js'

export type Item = { type: 'event' | 'transaction' | 'log'; payload: unknown }

/**
 * The NDJSON envelope, identical to the Python SDK's.
 *
 * One wire format for both sides: a browser event and a server event are the same shape, which
 * is what lets a trace hold both without the backend caring where an item came from.
 */
export function buildEnvelope(eventId: string, items: Item[]): string {
  const lines = [JSON.stringify({ event_id: eventId, sent_at: new Date().toISOString() })]

  for (const item of items) {
    const body = JSON.stringify(item.payload)
    // Length in the header: the parser must not have to scan for a newline inside a payload
    // that legitimately contains one.
    lines.push(JSON.stringify({ type: item.type, length: new Blob([body]).size }))
    lines.push(body)
  }

  return lines.join('\n')
}

/**
 * Send, surviving the page going away.
 *
 * `fetch` with `keepalive`, never `sendBeacon`. A beacon looks like the obvious choice and is
 * the wrong one here: it always sends with credentials mode "include", and CORS forbids a
 * wildcard `Access-Control-Allow-Origin` for a credentialed request. Every cross-origin beacon
 * is therefore rejected at the preflight — which is every real deployment, because the page is
 * on the customer's origin and Obsly is not.
 *
 * Worse, `sendBeacon` returns `true` the moment the browser queues the request. The CORS
 * failure happens afterwards and is unobservable, so a fallback behind that return value never
 * runs and the page load is lost in silence.
 *
 * `keepalive` outlives the document the same way, and unlike a beacon it takes headers and lets
 * credentials be turned off — which is what makes the wildcard legal. Its body limit is 64KB;
 * the span cap keeps an envelope far below that.
 */
export function send(dsn: Dsn, body: string): void {
  void fetch(dsn.envelopeUrl, {
    method: 'POST',
    // In a header, not the query string. A beacon could not set one, which is the only reason
    // the key was ever in a URL — and a URL is what ends up in access logs and referrers.
    headers: { 'Content-Type': 'application/x-obsly-envelope', 'X-Obsly-Key': dsn.publicKey },
    body,
    // Outlives the page. Without it a report sent on tab-hide is cancelled mid-flight, which
    // loses exactly the page loads worth measuring — the ones that ended in someone leaving.
    keepalive: true,
    // Never send cookies. The public key is the credential, an SDK that carried a customer's
    // session cookie to our origin would be a hole in their site, and "include" is what makes
    // the server's wildcard origin illegal.
    credentials: 'omit',
    mode: 'cors',
  }).catch(() => {
    // A reporting SDK that throws into the host application is worse than one that loses an
    // event. The page is not ours to break.
  })
}
