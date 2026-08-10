import { useState } from 'react'

import { api } from '../api'

/**
 * Sign-in. Deliberately not a link to the Django admin — the admin is an operator tool, not
 * the front door to the product.
 */
export function Login({ onSignedIn }: { onSignedIn: (username: string) => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const session = await api.login(username, password)
      onSignedIn(session.username ?? username)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not sign in.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="login" onSubmit={(event) => void submit(event)}>
      <h1 className="login__title">Sign in to Obsly</h1>

      <label className="login__field">
        <span>Username</span>
        <input
          name="username"
          autoComplete="username"
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          required
        />
      </label>

      <label className="login__field">
        <span>Password</span>
        <input
          name="password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          required
        />
      </label>

      {/* role=alert so a screen reader announces the failure rather than leaving the user
          wondering whether the button did anything. */}
      {error && (
        <p className="login__error" role="alert">
          {error}
        </p>
      )}

      <button className="button button--primary" type="submit" disabled={busy}>
        {busy ? 'Signing in…' : 'Sign in'}
      </button>
    </form>
  )
}
