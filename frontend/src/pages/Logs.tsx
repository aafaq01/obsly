import { useEffect, useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'

import { api, type LogRecord } from '../api'
import { LogList } from '../components/LogList'
import { Notice } from '../components/Notice'
import { handle } from '../errors'

// Ordered worst-last. "warning" means warning-and-worse: filtering to exactly one level hides
// the errors, which is never what somebody meant by it.
const LEVELS = ['trace', 'debug', 'info', 'warning', 'error', 'fatal']
const PERIODS = ['1h', '24h', '7d']

export function Logs() {
  const { projectId } = useParams()
  const [params, setParams] = useSearchParams()
  const [records, setRecords] = useState<LogRecord[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const id = Number(projectId)
  const level = params.get('level') ?? ''
  const period = params.get('period') ?? '24h'
  const traceId = params.get('trace_id') ?? ''
  const [query, setQuery] = useState(params.get('q') ?? '')

  useEffect(() => {
    let cancelled = false
    const search = new URLSearchParams({ period })
    if (level) search.set('level', level)
    if (query) search.set('q', query)
    if (traceId) search.set('trace_id', traceId)

    // Debounced so typing does not fire a request per keystroke.
    const timer = setTimeout(() => {
      api
        .logs(id, search)
        .then((next) => {
          if (!cancelled) setRecords(next)
        })
        .catch(handle(setError))
    }, 200)

    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [id, level, period, query, traceId])

  if (error) return <Notice>{error}</Notice>

  function update(key: string, value: string) {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next)
  }

  return (
    <>
      <h1 className="page-title">Logs</h1>
      <p className="page-subtitle">
        {traceId ? (
          <>
            Filtered to one request ·{' '}
            <button className="linklike" onClick={() => update('trace_id', '')}>
              show all
            </button>
          </>
        ) : (
          'What the application said — on the requests that succeeded as well as the ones that did not.'
        )}
      </p>

      <div className="filters">
        <input
          type="search"
          placeholder="Search message or logger"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          aria-label="Search logs"
        />
        <select value={level} onChange={(e) => update('level', e.target.value)} aria-label="Level">
          <option value="">All levels</option>
          {LEVELS.map((option) => (
            <option key={option} value={option}>
              {option} and worse
            </option>
          ))}
        </select>
        <select
          value={period}
          onChange={(e) => update('period', e.target.value)}
          aria-label="Period"
        >
          {PERIODS.map((option) => (
            <option key={option} value={option}>
              Last {option}
            </option>
          ))}
        </select>
      </div>

      {records === null ? (
        <Notice>Loading logs…</Notice>
      ) : records.length === 0 ? (
        <Notice>
          <strong>No logs yet</strong>
          Logs are off by default — they are the highest-volume signal by a wide margin. Set{' '}
          <code>enable_logs=True</code> in <code>obsly.init()</code>, and attach{' '}
          <code>ObslyLogHandler()</code> to forward the logging calls you already have.
        </Notice>
      ) : (
        <div className="card">
          <LogList records={records} projectId={id} />
        </div>
      )}
    </>
  )
}
