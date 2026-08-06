import { useEffect, useState } from 'react'

export interface Health {
  status: string
  database: string
}

type State =
  { kind: 'loading' } | { kind: 'ready'; health: Health } | { kind: 'error'; message: string }

/**
 * Placeholder shell. Its only job today is to prove the frontend can reach the backend —
 * the issue stream replaces it in `feat/web-issues`.
 */
export function App() {
  const [state, setState] = useState<State>({ kind: 'loading' })

  useEffect(() => {
    const controller = new AbortController()

    fetch('/health/', { signal: controller.signal })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`backend returned ${response.status}`)
        }
        return response.json() as Promise<Health>
      })
      .then((health) => setState({ kind: 'ready', health }))
      .catch((error: Error) => {
        // An abort is the component unmounting, not a failure worth rendering.
        if (error.name !== 'AbortError') {
          setState({ kind: 'error', message: error.message })
        }
      })

    return () => controller.abort()
  }, [])

  return (
    <main>
      <h1>Obsly</h1>
      {state.kind === 'loading' && <p role="status">Checking backend…</p>}
      {state.kind === 'ready' && (
        <p role="status">
          Backend {state.health.status}, database {state.health.database}
        </p>
      )}
      {state.kind === 'error' && <p role="alert">Backend unreachable: {state.message}</p>}
    </main>
  )
}
