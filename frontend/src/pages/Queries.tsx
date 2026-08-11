import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'

import { api, type SpanInsights } from '../api'
import { Magnitude } from '../components/Magnitude'
import { Notice, Skeleton } from '../components/Notice'
import { PeriodPicker } from '../components/PeriodPicker'
import { handle } from '../errors'
import { columnMax, formatMs } from '../format'

type SortKey = 'total_ms' | 'p95' | 'count' | 'per_transaction'

/**
 * Spans aggregated by what they do.
 *
 * A waterfall shows one request. The span that matters is usually the one that is individually
 * fast and runs ten thousand times, and only an aggregate can surface that.
 */
export function Queries() {
  const { projectId } = useParams()
  const [params, setParams] = useSearchParams()
  const [data, setData] = useState<SpanInsights | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sort, setSort] = useState<SortKey>('total_ms')

  const id = Number(projectId)
  const period = params.get('period') ?? '24h'
  const op = params.get('op') ?? ''

  useEffect(() => {
    let cancelled = false
    api
      .spans(id, period, op)
      .then((next) => {
        if (!cancelled) setData(next)
      })
      .catch(handle(setError))
    return () => {
      cancelled = true
    }
  }, [id, period, op])

  if (error) return <Notice>{error}</Notice>
  if (!data) return <Skeleton rows={6} />

  const rows = [...data.spans].sort((a, b) => b[sort] - a[sort])
  const maxTotal = columnMax(rows, (row) => row.total_ms)
  const maxP95 = columnMax(rows, (row) => row.p95)
  const maxCalls = columnMax(rows, (row) => row.count)

  function update(key: string, value: string) {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next)
  }

  return (
    <>
      <h1 className="page-title">Spans</h1>
      <p className="page-subtitle">
        Every span, grouped by what it does. The one worth fixing is usually not the slowest — it is
        the one that is fast and runs constantly.
      </p>

      <div className="filters">
        <select value={op} onChange={(e) => update('op', e.target.value)} aria-label="Operation">
          <option value="">All operations</option>
          {data.ops.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
        <PeriodPicker value={period} onChange={(next) => update('period', next)} />
        <select
          value={sort}
          onChange={(e) => setSort(e.target.value as SortKey)}
          aria-label="Sort by"
        >
          <option value="total_ms">Time spent</option>
          <option value="per_transaction">Calls per request</option>
          <option value="p95">p95</option>
          <option value="count">Calls</option>
        </select>
      </div>

      {rows.length === 0 ? (
        <Notice>
          <strong>No spans yet</strong>
          Spans come from tracing. Set <code>traces_sample_rate</code> in <code>obsly.init()</code>,
          and call <code>obsly.integrations.sqlalchemy.instrument()</code> to record database
          queries automatically.
        </Notice>
      ) : (
        <div className="card" style={{ overflowX: 'auto' }}>
          <table className="perf">
            <thead>
              <tr>
                <th>Span</th>
                <th className="num">Calls</th>
                <th className="num">Per request</th>
                <th className="num">p50</th>
                <th className="num">p95</th>
                <th className="num">Time spent</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={`${row.op}:${row.description}`}>
                  <td>
                    <span className="perf__name">{row.description || '(no description)'}</span>
                    <span className="perf__op">{row.op}</span>
                  </td>
                  <Magnitude value={row.count} max={maxCalls}>
                    {row.count.toLocaleString()}
                  </Magnitude>
                  {/* Above ~5 for a db.query this is the signature of an N+1: the same
                      statement running once per row of some earlier result. */}
                  <td className={`num ${row.per_transaction >= 5 ? 'bad' : ''}`}>
                    {row.per_transaction}
                    {row.per_transaction >= 5 && row.op === 'db.query' && (
                      <span className="tag-n1" title="Repeated many times within one request">
                        N+1?
                      </span>
                    )}
                  </td>
                  <td className="num">{formatMs(row.p50)}</td>
                  <Magnitude value={row.p95} max={maxP95} className="strong">
                    {formatMs(row.p95)}
                  </Magnitude>
                  <Magnitude value={row.total_ms} max={maxTotal}>
                    {formatMs(row.total_ms)}
                  </Magnitude>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="page-subtitle" style={{ marginTop: 14 }}>
        Looking for one request instead? <Link to={`/projects/${id}/traces`}>Traces</Link> shows the
        waterfall.
      </p>
    </>
  )
}
