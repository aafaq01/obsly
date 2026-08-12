import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'

import { Select } from '../components/Select'

import { api, type SpanInsights } from '../api'
import { Notice, Skeleton } from '../components/Notice'
import { RankChart } from '../components/RankChart'
import { handle } from '../errors'
import { formatMs } from '../format'

type SortKey = 'total_ms' | 'p95' | 'count' | 'per_transaction'

const SORT_LABEL: Record<SortKey, string> = {
  total_ms: 'time spent',
  p95: 'p95 duration',
  count: 'call count',
  per_transaction: 'calls per request',
}

const SORT_FORMAT: Record<SortKey, (value: number) => string> = {
  total_ms: (value) => formatMs(value),
  p95: (value) => formatMs(value),
  count: (value) => value.toLocaleString(),
  per_transaction: (value) => `${value}×`,
}

/**
 * Spans aggregated by what they do.
 *
 * A waterfall shows one request. The span that matters is usually the one that is individually
 * fast and runs ten thousand times, and only an aggregate can surface that.
 */
export function Queries({ layer = '' }: { layer?: string } = {}) {
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

  // A page called Database must not list http.client spans. The nav promises one tier of
  // the stack, and a page that quietly shows all of them makes the whole grouping a lie.
  const scoped = layer ? data.spans.filter((span) => span.op.startsWith(layer)) : data.spans
  const rows = [...scoped].sort((a, b) => b[sort] - a[sort])

  function update(key: string, value: string) {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next)
  }

  return (
    <>
      <h1 className="page-title">{layer === 'db.' ? 'Database' : 'Spans'}</h1>
      <p className="page-subtitle">
        {layer === 'db.'
          ? 'Every query, grouped by statement. The one worth fixing is usually not the slowest — it is the one that is fast and runs on every request.'
          : 'Every span, grouped by what it does. The one worth fixing is usually not the slowest — it is the one that is fast and runs constantly.'}
      </p>

      <div className="filters">
        <Select value={op} onChange={(e) => update('op', e.target.value)} aria-label="Operation">
          <option value="">All operations</option>
          {data.ops
            .filter((option) => !layer || option.startsWith(layer))
            .map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
        </Select>
        <Select
          value={sort}
          onChange={(e) => setSort(e.target.value as SortKey)}
          aria-label="Sort by"
        >
          <option value="total_ms">Time spent</option>
          <option value="per_transaction">Calls per request</option>
          <option value="p95">p95</option>
          <option value="count">Calls</option>
        </Select>
      </div>

      {rows.length === 0 ? (
        <Notice>
          <strong>No spans yet</strong>
          Spans come from tracing. Set <code>traces_sample_rate</code> in <code>obsly.init()</code>,
          and call <code>obsly.integrations.sqlalchemy.instrument()</code> to record database
          queries automatically.
        </Notice>
      ) : (
        <>
          <div className="card card--tight" style={{ marginBottom: 'var(--s4)' }}>
            <RankChart
              rows={rows.map((row) => ({
                label: row.description || '(no description)',
                sublabel: row.op,
                value: row[sort],
                href:
                  `/projects/${id}/span?period=${period}` +
                  `&op=${encodeURIComponent(row.op)}` +
                  `&description=${encodeURIComponent(row.description)}`,
              }))}
              format={SORT_FORMAT[sort]}
              caption={`Bar length is ${SORT_LABEL[sort]}, relative to the highest`}
            />
          </div>

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
                  <tr key={`${row.op}:${row.description}`} className="perf__row">
                    <td>
                      <Link
                        className="perf__link"
                        to={
                          `/projects/${id}/span?period=${period}` +
                          `&op=${encodeURIComponent(row.op)}` +
                          `&description=${encodeURIComponent(row.description)}`
                        }
                      >
                        <span className="perf__name">{row.description || '(no description)'}</span>
                        <span className="perf__op">{row.op}</span>
                      </Link>
                    </td>
                    <td className="num">{row.count.toLocaleString()}</td>
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
                    <td className="num strong">{formatMs(row.p95)}</td>
                    <td className="num">{formatMs(row.total_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <p className="page-subtitle" style={{ marginTop: 14 }}>
        Looking for one request instead? <Link to={`/projects/${id}/traces`}>Traces</Link> shows the
        waterfall.
      </p>
    </>
  )
}
