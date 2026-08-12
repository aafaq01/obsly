import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'

import { api, type DatabaseInsights } from '../api'
import { EventChart } from '../components/EventChart'
import { Notice, Skeleton } from '../components/Notice'
import { Sparkline } from '../components/Sparkline'
import { handle } from '../errors'
import { bucketLabel, formatMs } from '../format'
import { periodLabel } from '../periods'

type Ranking = 'slowest' | 'heaviest'

const RANKING: Record<Ranking, { label: string; explains: string }> = {
  slowest: {
    label: 'Slowest queries',
    explains:
      'Ranked by p95 — the statement that is individually painful. Usually fixed with an index or a smaller result set.',
  },
  heaviest: {
    label: 'Most time spent',
    explains:
      'Ranked by total time — the statement that is individually cheap and runs constantly. Usually fixed with a cache or by calling it less.',
  },
}

/**
 * The database tier.
 *
 * Two rankings, deliberately. The slowest query and the most expensive query are usually
 * different statements needing different fixes, and a page that shows one and calls it "top
 * queries" sends people to fix the wrong thing.
 */
export function Database() {
  const { projectId } = useParams()
  const id = Number(projectId)
  const [params] = useSearchParams()
  const period = params.get('period') ?? '24h'

  const [data, setData] = useState<DatabaseInsights | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [ranking, setRanking] = useState<Ranking>('slowest')

  useEffect(() => {
    let cancelled = false
    api
      .database(id, period)
      .then((next) => {
        if (!cancelled) setData(next)
      })
      .catch(handle(setError))
    return () => {
      cancelled = true
    }
  }, [id, period])

  if (error) return <Notice>{error}</Notice>
  if (!data) return <Skeleton rows={5} />

  const { headline } = data

  if (headline.queries === 0) {
    return (
      <>
        <div className="page-head">
          <h1>Database</h1>
        </div>
        <div className="card card--tight">
          <p className="logs__empty">
            No queries in the last {periodLabel(period)}. The Python SDK instruments SQLAlchemy
            automatically; anything else can be wrapped in{' '}
            <code>obsly.start_span(&quot;db.query&quot;, statement)</code>.
          </p>
        </div>
      </>
    )
  }

  return (
    <>
      <div className="page-head">
        <h1>Database</h1>
      </div>

      <dl className="meta">
        <Meta label="Queries" value={headline.queries.toLocaleString()} />
        <Meta label="Per request" value={`${headline.per_request}×`} />
        <Meta label="Time in queries" value={formatMs(headline.total_ms)} />
        <Meta label="p50" value={formatMs(headline.p50)} />
        <Meta label="p95" value={formatMs(headline.p95)} />
        <Meta label="Slowest" value={formatMs(headline.slowest)} />
      </dl>

      <div className="grid-2 section">
        <div>
          <h2 className="section__title">Queries per {bucketLabel(data.bucket_seconds)}</h2>
          <div className="card card--tight">
            <EventChart
              hourly={data.series.throughput}
              bucketSeconds={data.bucket_seconds}
              startedAt={data.series_start}
              unit="queries"
            />
          </div>
        </div>
        <div>
          <h2 className="section__title">p95 query duration</h2>
          <div className="card card--tight">
            {/* A single number cannot say whether it is getting worse. */}
            <Sparkline
              values={data.series.p95}
              bucketSeconds={data.bucket_seconds}
              startedAt={data.series_start}
              format={formatMs}
            />
          </div>
        </div>
      </div>

      {data.repeated.length > 0 && (
        <div className="section">
          <h2 className="section__title">Called once per row</h2>
          <p className="chart2__caption">
            Invisible in either ranking below: each call is fast, which is exactly why the pattern
            survives review. The number that gives it away is calls per request.
          </p>
          <div className="card">
            <table className="table">
              <thead>
                <tr>
                  <th>Statement</th>
                  <th>Table</th>
                  <th className="num strong">Per request</th>
                  <th className="num">Calls</th>
                  <th className="num">Time</th>
                  <th className="num">Recoverable</th>
                </tr>
              </thead>
              <tbody>
                {data.repeated.map((row) => (
                  <tr key={row.description}>
                    <td className="mono">
                      <Link to={statementLink(id, period, row)}>{row.description}</Link>
                    </td>
                    <td className="mono">{row.table || '—'}</td>
                    <td className="num strong">{row.per_request}×</td>
                    <td className="num">{row.count.toLocaleString()}</td>
                    <td className="num">{formatMs(row.total_ms)}</td>
                    {/* What collapsing the loop would give back — every call after the first,
                        not the whole total, because one query still has to run. */}
                    <td className="num">{formatMs(row.wasted_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="section">
        <div className="section__head">
          <h2 className="section__title">{RANKING[ranking].label}</h2>
          <div className="seg" role="group" aria-label="Ranking">
            {(Object.keys(RANKING) as Ranking[]).map((key) => (
              <button
                key={key}
                className={ranking === key ? 'seg__option seg__option--on' : 'seg__option'}
                aria-pressed={ranking === key}
                onClick={() => setRanking(key)}
              >
                {key === 'slowest' ? 'By p95' : 'By total time'}
              </button>
            ))}
          </div>
        </div>
        <p className="chart2__caption">{RANKING[ranking].explains}</p>

        <div className="card">
          <table className="table">
            <thead>
              <tr>
                <th>Statement</th>
                <th>Table</th>
                <th className="num">Calls</th>
                <th className="num">Per request</th>
                <th className="num">p50</th>
                <th className="num strong">p95</th>
                <th className="num">Total</th>
              </tr>
            </thead>
            <tbody>
              {data[ranking].map((row) => (
                <tr key={row.description}>
                  <td className="mono">
                    <Link to={statementLink(id, period, row)}>{row.description}</Link>
                  </td>
                  <td className="mono">{row.table || '—'}</td>
                  <td className="num">{row.count.toLocaleString()}</td>
                  <td className="num">{row.per_request}×</td>
                  <td className="num">{formatMs(row.p50)}</td>
                  <td className="num strong">{formatMs(row.p95)}</td>
                  <td className="num">{formatMs(row.total_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {data.tables.length > 0 && (
        <div className="section">
          <h2 className="section__title">By table</h2>
          <p className="chart2__caption">
            A statement is the unit you fix; a table is the unit you reason about when deciding
            where an index goes.
          </p>
          <div className="card">
            <table className="table">
              <thead>
                <tr>
                  <th>Table</th>
                  <th className="num">Statements</th>
                  <th className="num">Calls</th>
                  <th className="num strong">Time</th>
                  <th className="num">Slowest</th>
                </tr>
              </thead>
              <tbody>
                {data.tables.map((row) => (
                  <tr key={row.table}>
                    <td className="mono">{row.table}</td>
                    <td className="num">{row.statements}</td>
                    <td className="num">{row.count.toLocaleString()}</td>
                    <td className="num strong">{formatMs(row.total_ms)}</td>
                    <td className="num">{formatMs(row.slowest)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  )
}

/** Through to the span page: which endpoints call it, its distribution, traces to open.
 *  Takes only the two fields that identify a span, so both tables can use it. */
function statementLink(
  id: number,
  period: string,
  row: { op: string; description: string },
): string {
  return (
    `/projects/${id}/span?period=${period}` +
    `&op=${encodeURIComponent(row.op)}` +
    `&description=${encodeURIComponent(row.description)}`
  )
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="meta__item">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  )
}
