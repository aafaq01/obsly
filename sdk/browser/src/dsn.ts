export interface Dsn {
  origin: string
  publicKey: string
  projectId: string
  envelopeUrl: string
}

/**
 * `https://<public key>@<host>/<project id>`
 *
 * The same string the Python SDK takes, so one project's DSN works from either side and
 * nobody has to learn two formats.
 */
export function parseDsn(raw: string): Dsn {
  const url = new URL(raw.trim())

  const publicKey = url.username
  if (!publicKey) throw new Error('DSN is missing its public key')

  const projectId = url.pathname.replace(/^\/+|\/+$/g, '')
  if (!projectId) throw new Error('DSN is missing its project id')

  // The credential is stripped from the origin deliberately: it travels in a header, and a URL
  // carrying it would end up in referrer logs and devtools traces.
  const origin = `${url.protocol}//${url.host}`

  return { origin, publicKey, projectId, envelopeUrl: `${origin}/api/${projectId}/envelope/` }
}
