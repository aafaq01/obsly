import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'

import { api, type Performance as PerformanceData } from '../api'
import { EventChart } from '../components/EventChart'
import { Notice } from '../components/Notice'
import { handle } from '../errors'

const PERIODS = ['1h', '24h', '7d', '30d']

type SortKey = 'total_ms' | 'p95' | 'count' | 'failure_rate'

export function Performance() {
  const { projectId } = useParams()
  const [data, setData] = useState<PerformanceData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [period, setPeriod] = useState('24h')
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
  if (!data) return <Notice>Loading performance…</Notice>

  const rows = [...data.endpoints].sort((a, b) => b[sort] - a[sort])

  return (
    <>
      <h1 className="page-title">Performance</h1>
      <p className="page-subtitle">
        Latency percentiles per endpoint. Percentiles rather than averages — a mean sits where
        nobody&rsquo;s request actually landed.
      </p>

      <div className="filters">
        <select value={period} onChange={(e) => setPeriod(e.target.value)} aria-label="Period">
          {PERIODS.map((option) => (
            <option key={option} value={option}>
              Last {option}
            </option>
          ))}
        </select>
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
          <h2 className="section__title">Throughput per hour</h2>
          <div className="card" style={{ padding: 16 }}>
            <EventChart hourly={data.summary.hourly} />
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
                  <tr key={`${row.op}:${row.name}`}>
                    <td>
                      <span className="perf__name">{row.name}</span>
                      <span className="perf__op">{row.op}</span>
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
