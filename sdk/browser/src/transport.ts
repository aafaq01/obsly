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
 * Send, preferring `sendBeacon` when the page is going away.
 *
 * A pagehide is exactly when the numbers matter — a load that ended in the reader leaving is a
 * load worth measuring — and it is also when `fetch` is cancelled. `sendBeacon` survives the
 * unload; `keepalive` is the fallback for browsers that reject the beacon's size.
 */
export function send(dsn: Dsn, body: string, { beacon = false } = {}): void {
  const url = `${dsn.envelopeUrl}?obsly_key=${encodeURIComponent(dsn.publicKey)}`

  if (beacon && typeof navigator.sendBeacon === 'function') {
    // The key travels in the query string here: a beacon cannot set headers at all.
    const blob = new Blob([body], { type: 'application/x-obsly-envelope' })
    if (navigator.sendBeacon(url, blob)) return
  }

  void fetch(dsn.envelopeUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-obsly-envelope', 'X-Obsly-Key': dsn.publicKey },
    body,
    keepalive: true,
    // Never send cookies. The public key is the credential, and an SDK that carried a
    // customer's session cookie to our origin would be a hole in their site, not a feature.
    credentials: 'omit',
    mode: 'cors',
  }).catch(() => {
    // A reporting SDK that throws into the host application is worse than one that loses an
    // event. The page is not ours to break.
  })
}
