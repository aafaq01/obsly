import { useEffect, useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'

import { api, type LogRecord } from '../api'
import { LogList } from '../components/LogList'
import { Notice, Skeleton } from '../components/Notice'
import { handle } from '../errors'

// Worst last, matching the server's ordering.
const LEVELS = ['trace', 'debug', 'info', 'warning', 'error', 'fatal']
export function Logs() {
  const { projectId } = useParams()
  const [params, setParams] = useSearchParams()
  const [records, setRecords] = useState<LogRecord[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const id = Number(projectId)
  const levelsParam = params.get('levels') ?? ''
  const selected = levelsParam.split(',').filter(Boolean)
  const period = params.get('period') ?? '24h'
  const traceId = params.get('trace_id') ?? ''
  const [query, setQuery] = useState(params.get('q') ?? '')

  useEffect(() => {
    let cancelled = false
    const search = new URLSearchParams({ period })
    if (levelsParam) search.set('levels', levelsParam)
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
  }, [id, levelsParam, period, query, traceId])

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
          placeholder="Search message, logger or attributes"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          aria-label="Search logs"
        />
      </div>

      {/* Toggles rather than a dropdown. "warning and worse" and "only warnings" are different
          questions, and a single-select control can only ask one of them. */}
      <div className="levelbar">
        <button
          className={selected.length === 0 ? 'chip chip--on' : 'chip'}
          onClick={() => update('levels', '')}
        >
          All
        </button>
        {LEVELS.map((option) => {
          const on = selected.includes(option)
          return (
            <button
              key={option}
              className={on ? `chip chip--on chip--${option}` : `chip chip--${option}`}
              aria-pressed={on}
              onClick={() =>
                update(
                  'levels',
                  (on ? selected.filter((l) => l !== option) : [...selected, option]).join(','),
                )
              }
            >
              {option}
            </button>
          )
        })}
      </div>

      {records === null ? (
        <Skeleton rows={8} />
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
