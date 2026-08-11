import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'

import { api, type Performance as PerformanceData } from '../api'
import { EventChart } from '../components/EventChart'
import { Notice, Skeleton } from '../components/Notice'
import { PeriodPicker } from '../components/PeriodPicker'
import { RankChart } from '../components/RankChart'
import { handle } from '../errors'
import { periodLabel } from '../periods'
import { bucketLabel, formatMs } from '../format'

type SortKey = 'total_ms' | 'p95' | 'count' | 'failure_rate'

const SORT_LABEL: Record<SortKey, string> = {
  total_ms: 'time spent',
  p95: 'p95 latency',
  count: 'request count',
  failure_rate: 'failure rate',
}

const SORT_FORMAT: Record<SortKey, (value: number) => string> = {
  total_ms: (value) => formatMs(value),
  p95: (value) => formatMs(value),
  count: (value) => value.toLocaleString(),
  failure_rate: (value) => `${(value * 100).toFixed(1)}%`,
}

export function Performance() {
  const { projectId } = useParams()
  const [data, setData] = useState<PerformanceData | null>(null)
  const [error, setError] = useState<string | null>(null)
  // In the URL, not in state: a breadcrumb back from a detail page has to restore the
  // window you were looking at, and state cannot survive that trip.
  const [params, setParams] = useSearchParams()
  const period = params.get('period') ?? '24h'
  const [sort, setSort] = useState<SortKey>('total_ms')

  const id = Number(projectId)

  useEffect(() => {
    // Not setData(null) synchronously: setState directly in an effect body cascades renders.
    // Clearing inside the promise also means the old numbers stay on screen while the new
    // period loads, instead of the page flashing empty.
    let cancelled = false
    api
      .performance(id, period)
      .then((next) => {
        if (!cancelled) setData(next)
      })
      .catch(handle(setError))

    return () => {
      cancelled = true
    }
  }, [id, period])

  if (error) return <Notice>{error}</Notice>
  if (!data) return <Skeleton rows={6} />

  const rows = [...data.endpoints].sort((a, b) => b[sort] - a[sort])

  return (
    <>
      <h1 className="page-title">Performance</h1>
      <p className="page-subtitle">
        Latency percentiles per endpoint. Percentiles rather than averages — a mean sits where
        nobody&rsquo;s request actually landed.
      </p>

      <div className="filters">
        <PeriodPicker
          value={period}
          onChange={(next) => {
            const updated = new URLSearchParams(params)
            updated.set('period', next)
            setParams(updated)
          }}
        />
        <select
          value={sort}
          onChange={(e) => setSort(e.target.value as SortKey)}
          aria-label="Sort by"
        >
          <option value="total_ms">Time spent</option>
          <option value="p95">p95</option>
          <option value="count">Throughput</option>
          <option value="failure_rate">Failure rate</option>
        </select>
      </div>

      <div className="stat-row">
        <Stat label="Transactions" value={data.summary.transactions.toLocaleString()} />
        <Stat label="Per minute" value={data.summary.throughput_per_minute.toFixed(2)} />
        <Stat label="Failure rate" value={percent(data.summary.failure_rate)} />
      </div>

      {data.summary.transactions > 0 && (
        <div className="section">
          <h2 className="section__title">
            Throughput per {bucketLabel(data.summary.bucket_seconds)}
          </h2>
          <div className="card" style={{ padding: 16 }}>
            <EventChart
              hourly={data.summary.series}
              bucketSeconds={data.summary.bucket_seconds}
              startedAt={data.summary.series_start}
              unit="requests"
              caption={`Requests handled per ${bucketLabel(data.summary.bucket_seconds)} across every endpoint, over the last ${periodLabel(period)}.`}
            />
          </div>
        </div>
      )}

      {rows.length > 0 && (
        <div className="section">
          <h2 className="section__title">Top endpoints by {SORT_LABEL[sort]}</h2>
          <div className="card card--tight">
            {/* Ranked by whatever the table is sorted by, so the chart and the table can never
              tell different stories about the same window. */}
            <RankChart
              rows={rows.map((row) => ({
                label: row.name,
                sublabel: row.op,
                value: row[sort],
              }))}
              format={SORT_FORMAT[sort]}
              caption={`Bar length is ${SORT_LABEL[sort]}, relative to the highest`}
            />
          </div>
        </div>
      )}

      <div className="section">
        <h2 className="section__title">Endpoints</h2>
        {rows.length === 0 ? (
          <Notice>
            <strong>No transactions yet</strong>
            Tracing is off by default. Set <code>traces_sample_rate</code> in{' '}
            <code>obsly.init()</code> — start low, it multiplies volume by your request rate.
          </Notice>
        ) : (
          <div className="card" style={{ overflowX: 'auto' }}>
            <table className="perf">
              <thead>
                <tr>
                  <th>Endpoint</th>
                  <th className="num">tpm</th>
                  <th className="num">p50</th>
                  <th className="num">p75</th>
                  <th className="num">p95</th>
                  <th className="num">p99</th>
                  <th className="num">Failures</th>
                  <th className="num">Time spent</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={`${row.op}:${row.name}`} className="perf__row">
                    <td>
                      <Link
                        className="perf__link"
                        to={
                          `/projects/${id}/endpoint?period=${period}` +
                          `&name=${encodeURIComponent(row.name)}` +
                          `&op=${encodeURIComponent(row.op)}`
                        }
                      >
                        <span className="perf__name">{row.name}</span>
                        <span className="perf__op">{row.op}</span>
                      </Link>
                    </td>
                    <td className="num">{row.throughput_per_minute.toFixed(2)}</td>
                    <td className="num">{ms(row.p50)}</td>
                    <td className="num">{ms(row.p75)}</td>
                    <td className="num strong">{ms(row.p95)}</td>
                    <td className="num">{ms(row.p99)}</td>
                    <td className={`num ${row.failure_rate > 0 ? 'bad' : ''}`}>
                      {percent(row.failure_rate)}
                    </td>
                    <td className="num">{seconds(row.total_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="stat__value">{value}</div>
      <div className="stat__label">{label}</div>
    </div>
  )
}

/** Sub-millisecond values round to "0ms", which reads as "not measured" rather than "fast". */
function ms(value: number): string {
  if (value > 0 && value < 1) return '<1ms'
  if (value >= 1000) return `${(value / 1000).toFixed(2)}s`
  return `${Math.round(value)}ms`
}

function seconds(total: number): string {
  if (total >= 60_000) return `${(total / 60_000).toFixed(1)}min`
  if (total >= 1000) return `${(total / 1000).toFixed(1)}s`
  return `${Math.round(total)}ms`
}

function percent(rate: number): string {
  if (rate === 0) return '0%'
  if (rate < 0.001) return '<0.1%'
  return `${(rate * 100).toFixed(1)}%`
}
