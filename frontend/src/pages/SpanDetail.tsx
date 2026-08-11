import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'

import { api, type SpanDetail as Detail } from '../api'
import { Notice, Skeleton } from '../components/Notice'
import { handle } from '../errors'
import { columnMax, formatMs } from '../format'
import { relativeTime } from '../time'

/**
 * One span group.
 *
 * The aggregate table says a query is expensive. This page says *which endpoints* make it
 * expensive and hands over a real trace to open — the step between "this is the problem" and
 * "here is the request where it happened".
 */
export function SpanDetail() {
  const { projectId } = useParams()
  const [params] = useSearchParams()
  const [detail, setDetail] = useState<Detail | null>(null)
  const [error, setError] = useState<string | null>(null)

  const id = Number(projectId)
  const op = params.get('op') ?? ''
  const description = params.get('description') ?? ''
  const period = params.get('period') ?? '24h'

  useEffect(() => {
    let cancelled = false
    api
      .spanDetail(id, period, op, description)
      .then((next) => {
        if (!cancelled) setDetail(next)
      })
      .catch(handle(setError))
    return () => {
      cancelled = true
    }
  }, [id, period, op, description])

  if (error) return <Notice>{error}</Notice>
  if (!detail) return <Skeleton rows={5} />

  const { summary, distribution, callers, samples } = detail
  const peak = columnMax(distribution, (bucket) => bucket.count)
  const callerMax = columnMax(callers, (caller) => caller.total_ms)

  return (
    <>
      <p className="crumb">
        <Link to={`/projects/${id}/spans?period=${period}`}>← Spans</Link>
      </p>

      <h1 className="detail-title mono-title">{description || detail.op}</h1>
      <div className="detail-culprit">{detail.op}</div>

      <dl className="meta">
        <Meta label="Calls" value={summary.count.toLocaleString()} />
        <Meta label="Per request" value={String(summary.per_transaction)} />
        <Meta label="p50" value={formatMs(summary.p50)} />
        <Meta label="p95" value={formatMs(summary.p95)} />
        <Meta label="p99" value={formatMs(summary.p99)} />
        <Meta label="Slowest" value={formatMs(summary.slowest)} />
        <Meta label="Time spent" value={formatMs(summary.total_ms)} />
      </dl>

      <div className="section">
        <h2 className="section__title">Duration distribution</h2>
        <div className="card card--tight">
          {/* A p50 and a p95 describe two points. The shape between them says whether this is
              one slow tail or two behaviours wearing the same statement — and those need
              different fixes. */}
          <div
            className="dist"
            role="img"
            aria-label={`Duration distribution, ${summary.count} calls`}
          >
            {distribution.map((bucket, index) => (
              <div
                className="dist__slot"
                key={index}
                title={`${bucket.count} calls between ${formatMs(bucket.from_ms)} and ${formatMs(bucket.to_ms)}`}
              >
                <div
                  className="dist__bar"
                  style={{ height: bucket.count === 0 ? '1px' : `${(bucket.count / peak) * 100}%` }}
                />
              </div>
            ))}
          </div>
          <div className="chart2__xaxis" style={{ paddingLeft: 0 }}>
            <span>{formatMs(0)}</span>
            <span>{formatMs(summary.slowest / 2)}</span>
            <span>{formatMs(summary.slowest)}</span>
          </div>
        </div>
      </div>

      <div className="grid-2 section">
        <div>
          <h2 className="section__title">Which endpoints call it</h2>
          <div className="card">
            {callers.map((caller) => (
              <div className="mini-row" key={caller.transaction}>
                <span className="mini-row__title mono">{caller.transaction}</span>
                <span className="mini-row__num">
                  {formatMs(caller.total_ms)}
                  <em>
                    {caller.count.toLocaleString()} calls ·{' '}
                    {Math.round((caller.total_ms / callerMax) * 100)}%
                  </em>
                </span>
              </div>
            ))}
          </div>
        </div>

        <div>
          <h2 className="section__title">Traces to open · slowest first</h2>
          <div className="card">
            {samples.map((sample) => (
              <Link
                className="mini-row"
                key={sample.transaction_id}
                to={`/projects/${id}/traces/${sample.transaction_id}`}
              >
                <span className="mini-row__main">
                  <span className="mini-row__title mono">{sample.transaction}</span>
                </span>
                <span className="mini-row__num">
                  {formatMs(sample.duration_ms)}
                  <em>
                    of {formatMs(sample.transaction_ms)} · {relativeTime(sample.timestamp)}
                  </em>
                </span>
              </Link>
            ))}
          </div>
        </div>
      </div>
    </>
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
