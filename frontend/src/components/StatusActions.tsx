import { useState } from 'react'

import { api } from '../api'

const NEXT: Record<string, { label: string; status: string }[]> = {
  unresolved: [
    { label: 'Resolve', status: 'resolved' },
    { label: 'Ignore', status: 'ignored' },
  ],
  resolved: [{ label: 'Reopen', status: 'unresolved' }],
  ignored: [{ label: 'Unignore', status: 'unresolved' }],
}

interface Props {
  issueId: number
  status: string
  onChange: (status: string) => void
}

/**
 * Only the transitions available from the current state are offered, so "Resolve" never
 * appears on something already resolved. A disabled or no-op button trains people to
 * distrust the whole row.
 */
export function StatusActions({ issueId, status, onChange }: Props) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function apply(next: string) {
    setBusy(true)
    setError(null)
    try {
      const updated = await api.setIssueStatus(issueId, next)
      onChange(updated.status)
    } catch (cause) {
      // The status stays as it was. Showing the new state before the server agreed would be
      // a lie the next page load silently corrects.
      setError(cause instanceof Error ? cause.message : 'could not update')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="actions">
      {(NEXT[status] ?? []).map((action) => (
        <button
          key={action.status}
          className="button"
          disabled={busy}
          onClick={() => void apply(action.status)}
        >
          {action.label}
        </button>
      ))}
      <span className={`status-pill status-pill--${status}`}>{status}</span>
      {error && <span className="actions__error">{error}</span>}
    </div>
  )
}
