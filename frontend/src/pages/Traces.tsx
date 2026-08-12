import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'

import { Select } from '../components/Select'

import { api, type TraceSummary } from '../api'
import { Notice, Skeleton } from '../components/Notice'
import { handle } from '../errors'
import { formatMs } from '../format'
import { relativeTime } from '../time'

/** What each root op is, in the words the sidebar uses. */
const TIER: Record<string, string> = {
  pageload: 'browser',
  navigation: 'browser',
  'http.server': 'backend',
}

export function Traces() {
  const { projectId } = useParams()
  const [params, setParams] = useSearchParams()
  const [traces, setTraces] = useState<TraceSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const id = Number(projectId)
  const sort = params.get('sort') ?? 'slowest'
  const status = params.get('status') ?? 'all'
  const name = params.get('name') ?? ''

  useEffect(() => {
    let cancelled = false
    const query = new URLSearchParams({ period: '24h' })
    if (sort === 'recent') query.set('sort', 'recent')
    if (status === 'failed') query.set('status', 'failed')
    if (name) query.set('name', name)

    api
      .traces(id, query)
      .then((next) => {
        if (!cancelled) setTraces(next)
      })
      .catch(handle(setError))

    return () => {
      cancelled = true
    }
  }, [id, sort, status, name])

  if (error) return <Notice>{error}</Notice>

  function update(key: string, value: string) {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next)
  }

  return (
    <>
      <h1 className="page-title">Traces</h1>
      <p className="page-subtitle">
        {name ? (
          <>
            Filtered to <code>{name}</code> ·{' '}
            <button className="linklike" onClick={() => update('name', '')}>
              clear
            </button>
          </>
        ) : (
          'One row per request. Open one to see where its time went.'
        )}
      </p>

      <div className="filters">
        <Select value={sort} onChange={(e) => update('sort', e.target.value)} aria-label="Sort by">
          <option value="slowest">Slowest first</option>
          <option value="recent">Most recent</option>
        </Select>
        <Select
          value={status}
          onChange={(e) => update('status', e.target.value)}
          aria-label="Status"
        >
          <option value="all">All</option>
          <option value="failed">Failed only</option>
        </Select>
      </div>

      {traces === null ? (
        <Skeleton rows={6} />
      ) : traces.length === 0 ? (
        <Notice>
          <strong>No traces yet</strong>
          Tracing is off by default. Set <code>traces_sample_rate</code> in{' '}
          <code>obsly.init()</code> to start recording.
        </Notice>
      ) : (
        <div className="card">
          {traces.map((trace) => (
            <Link to={`/projects/${id}/traces/${trace.id}`} className="trace-row" key={trace.id}>
              <div>
                <div className="trace-row__name">{trace.name}</div>
                <div className="issue-row__meta">
                  <span className={`level level--${trace.status === 'ok' ? 'info' : 'error'}`}>
                    {trace.status}
                  </span>
                  {/* Which side of the stack this came from. A browser page load and a server
                      request sitting unlabelled in one list, sorted by duration, puts every
                      page load on top — they are seconds and requests are milliseconds — and
                      reads as the backend having got dramatically slower. */}
                  <span className="tag tag--muted">{TIER[trace.op] ?? trace.op}</span>
                  <span>
                    {trace.span_count === 0 ? 'nothing instrumented' : `${trace.span_count} spans`}
                  </span>
                  {trace.release && <span>{trace.release}</span>}
                  <span>{relativeTime(trace.timestamp)}</span>
                </div>
              </div>
              <div className="issue-row__num">
                {formatMs(trace.duration_ms)}
                <span>duration</span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </>
  )
}
