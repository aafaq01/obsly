import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'

import { api, type Dashboard as Data } from '../api'
import { Notice } from '../components/Notice'
import { PeriodPicker } from '../components/PeriodPicker'
import { Sparkline } from '../components/Sparkline'
import { handle } from '../errors'
import { bucketLabel, formatMs } from '../format'
import { relativeTime } from '../time'

export function Dashboard() {
  const { projectId } = useParams()
  const [params, setParams] = useSearchParams()
  const [data, setData] = useState<Data | null>(null)
  const [error, setError] = useState<string | null>(null)

  const id = Number(projectId)
  const period = params.get('period') ?? '24h'

  useEffect(() => {
    let cancelled = false
    api
      .dashboard(id, period)
      .then((next) => {
        if (!cancelled) setData(next)
      })
      .catch(handle(setError))
    return () => {
      cancelled = true
    }
  }, [id, period])

  if (error) return <Notice>{error}</Notice>
  if (!data) return <Notice>Loading dashboard…</Notice>

  const { headline, series } = data
  const empty = headline.transactions === 0 && headline.errors === 0 && headline.logs === 0

  return (
    <>
      <h1 className="page-title">Overview</h1>
      <p className="page-subtitle">Everything this project reported in the selected window.</p>

      {/* One filter row above everything it scopes, so every chart re-renders against the
          same slice. Per-chart period pickers make two charts disagree silently. */}
      <div className="filters">
        <PeriodPicker
          value={period}
          onChange={(next) => {
            const params2 = new URLSearchParams(params)
            params2.set('period', next)
            setParams(params2)
          }}
        />
      </div>

      {empty && (
        <Notice>
          <strong>Nothing reported yet</strong>
          Point an SDK at this project. Tracing and logs are both off by default — set{' '}
          <code>traces_sample_rate</code> and <code>enable_logs</code> in <code>obsly.init()</code>.
        </Notice>
      )}

      <div className="tiles">
        <Tile label="Requests" value={headline.transactions.toLocaleString()} />
        <Tile label="Per minute" value={headline.throughput_per_minute.toFixed(2)} />
        <Tile
          label="Failure rate"
          value={percent(headline.failure_rate)}
          bad={headline.failure_rate > 0}
        />
        <Tile label="p95 latency" value={formatMs(headline.p95_ms)} />
        <Tile label="Errors" value={headline.errors.toLocaleString()} />
        <Tile
          label="Unresolved issues"
          value={headline.unresolved_issues.toLocaleString()}
          bad={headline.unresolved_issues > 0}
        />
        <Tile label="Log records" value={headline.logs.toLocaleString()} />
      </div>

      {/* Small multiples rather than one chart with five lines. Throughput and latency have
          different units, and putting them on one plot would need a second y-axis — which
          invents a correlation that is not in the data. */}
      <div className="charts">
        <Chart title={`Requests per ${bucketLabel(data.bucket_seconds)}`}>
          <Sparkline values={series.throughput} bucketSeconds={data.bucket_seconds} unit=" req" />
        </Chart>
        <Chart title={`p95 latency per ${bucketLabel(data.bucket_seconds)}`}>
          <Sparkline values={series.p95} bucketSeconds={data.bucket_seconds} format={formatMs} />
        </Chart>
        <Chart title={`Failed requests per ${bucketLabel(data.bucket_seconds)}`}>
          <Sparkline
            values={series.failures}
            bucketSeconds={data.bucket_seconds}
            unit=" failed"
            tone="critical"
          />
        </Chart>
        <Chart title={`Errors captured per ${bucketLabel(data.bucket_seconds)}`}>
          <Sparkline
            values={series.errors}
            bucketSeconds={data.bucket_seconds}
            unit=" errors"
            tone="critical"
          />
        </Chart>
        <Chart title={`Log records per ${bucketLabel(data.bucket_seconds)}`}>
          <Sparkline values={series.logs} bucketSeconds={data.bucket_seconds} unit=" logs" />
        </Chart>
      </div>

      <div className="grid-2 section">
        <div>
          <h2 className="section__title">Top unresolved issues</h2>
          <div className="card">
            {data.top_issues.length === 0 ? (
              <p className="logs__empty">Nothing unresolved.</p>
            ) : (
              data.top_issues.map((issue) => (
                <Link className="mini-row" to={`/projects/${id}/issues/${issue.id}`} key={issue.id}>
                  <div className="mini-row__main">
                    <span className={`level level--${issue.level}`}>{issue.level}</span>
                    <span className="mini-row__title">{issue.title}</span>
                  </div>
                  <span className="mini-row__num">
                    {issue.times_seen.toLocaleString()}
                    <em>{relativeTime(issue.last_seen)}</em>
                  </span>
                </Link>
              ))
            )}
          </div>
        </div>

        <div>
          <h2 className="section__title">Slowest endpoints (p95)</h2>
          <div className="card">
            {data.slowest_endpoints.length === 0 ? (
              <p className="logs__empty">No transactions recorded.</p>
            ) : (
              data.slowest_endpoints.map((endpoint) => (
                <div className="mini-row" key={endpoint.name}>
                  <span className="mini-row__title mono">{endpoint.name}</span>
                  <span className="mini-row__num">
                    {formatMs(endpoint.p95)}
                    <em>{endpoint.count.toLocaleString()} req</em>
                  </span>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </>
  )
}

function Tile({ label, value, bad }: { label: string; value: string; bad?: boolean }) {
  return (
    <div className="tile">
      {/* Proportional figures on a large standalone number — tabular digits make it look
          loose at display sizes. */}
      <div className={bad ? 'tile__value tile__value--bad' : 'tile__value'}>{value}</div>
      <div className="tile__label">{label}</div>
    </div>
  )
}

function Chart({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="chartcard">
      <h3 className="chartcard__title">{title}</h3>
      {children}
    </div>
  )
}

function percent(rate: number): string {
  if (rate === 0) return '0%'
  if (rate < 0.001) return '<0.1%'
  return `${(rate * 100).toFixed(1)}%`
}
