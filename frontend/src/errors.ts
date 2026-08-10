import { UnauthorizedError } from './api'

/** Turn a rejected request into a message a person can act on.
 *
 * An expired session is called out separately: "sign in again" and "the server is broken" need
 * different reactions, and collapsing them into one message costs the user the difference. */
export function handle(setError: (message: string) => void) {
  return (error: Error) => {
    setError(
      error instanceof UnauthorizedError
        ? 'Your session expired. Sign in again to continue.'
        : `Could not load: ${error.message}`,
    )
  }
}
