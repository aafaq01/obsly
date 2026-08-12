import { useState } from 'react'

import { api } from '../api'

/**
 * Sign-in, and first-run registration.
 *
 * Deliberately not a link to the Django admin — the admin is an operator tool, not the front
 * door to the product.
 *
 * One form in two modes rather than two components. They differ by a verb, an autocomplete
 * hint and one sentence; splitting them would be two places for the same field list to drift.
 *
 * An install with no users opens in `register`, because a sign-in form is a door with no key
 * cut for it — the alternative was telling people to run `createsuperuser` inside a container
 * before they could see anything.
 */
export function Login({
  onSignedIn,
  signupOpen = false,
  firstRun = false,
}: {
  onSignedIn: (username: string) => void
  signupOpen?: boolean
  firstRun?: boolean
}) {
  const [mode, setMode] = useState<'signin' | 'register'>(firstRun ? 'register' : 'signin')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const registering = mode === 'register'

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const session = registering
        ? await api.register(username, password)
        : await api.login(username, password)
      onSignedIn(session.username ?? username)
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : registering
            ? 'Could not create the account.'
            : 'Could not sign in.',
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="login" onSubmit={(event) => void submit(event)}>
      <h1 className="login__title">{registering ? 'Create your account' : 'Sign in to Obsly'}</h1>

      {firstRun && registering && (
        <p className="login__lede">
          Nobody owns this install yet, so the first account gets full access to it. Everything it
          will hold — stack traces, queries, logs — comes from your own production, so pick a
          password accordingly.
        </p>
      )}

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
          autoComplete={registering ? 'new-password' : 'current-password'}
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
        {busy
          ? registering
            ? 'Creating account…'
            : 'Signing in…'
          : registering
            ? 'Create account'
            : 'Sign in'}
      </button>

      {/* Offered only when the server says registration is open. A link that leads to a 403 is
          worse than no link, and on a closed install there is nothing here to choose. */}
      {signupOpen && !firstRun && (
        <button
          className="login__switch"
          type="button"
          onClick={() => {
            setMode(registering ? 'signin' : 'register')
            setError(null)
          }}
        >
          {registering ? 'I already have an account' : 'Create an account'}
        </button>
      )}
    </form>
  )
}
