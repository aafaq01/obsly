import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'

import { api, type EndpointDetail as Detail } from '../api'
import { Breadcrumbs } from '../components/Breadcrumbs'
import { Distribution } from '../components/Distribution'
import { Notice, Skeleton } from '../components/Notice'
import { RankChart } from '../components/RankChart'
import { handle } from '../errors'
import { formatMs } from '../format'
import { absoluteTime, relativeTime } from '../time'

/**
 * One endpoint.
 *
 * The table ranks endpoints against each other; this answers the question that always comes
 * next — "and what is it doing" — with the operations inside it, the shape of its latency, and
 * a request to read.
 */
export function EndpointDetail() {
  const { projectId } = useParams()
  const [params] = useSearchParams()
  const [detail, setDetail] = useState<Detail | null>(null)
  const [error, setError] = useState<string | null>(null)

  const id = Number(projectId)
  const name = params.get('name') ?? ''
  const period = params.get('period') ?? '24h'
  // The summary table groups by (name, op); without the op two rows sharing a name would
  // both land on merged figures matching neither.
  const op = params.get('op') ?? ''

  useEffect(() => {
    let cancelled = false
    api
      .endpointDetail(id, period, name, op)
      .then((next) => {
        if (!cancelled) setDetail(next)
      })
      .catch(handle(setError))
    return () => {
      cancelled = true
    }
  }, [id, period, name, op])

  if (error) return <Notice>{error}</Notice>
  if (!detail) return <Skeleton rows={5} />

  const { summary, spans, samples } = detail

  return (
    <>
      <Breadcrumbs
        trail={[
          { label: 'Performance', to: `/projects/${id}/performance?period=${period}` },
          { label: detail.name },
        ]}
      />

      <h1 className="detail-title mono-title">{detail.name}</h1>

      <dl className="meta">
        <Meta label="Requests" value={summary.count.toLocaleString()} />
        <Meta label="Per minute" value={summary.throughput_per_minute.toFixed(2)} />
        <Meta label="Failure rate" value={`${(summary.failure_rate * 100).toFixed(1)}%`} />
        <Meta label="p50" value={formatMs(summary.p50)} />
        <Meta label="p95" value={formatMs(summary.p95)} />
        <Meta label="p99" value={formatMs(summary.p99)} />
        <Meta label="Slowest" value={formatMs(summary.slowest)} />
      </dl>

      <div className="section">
        <h2 className="section__title">How long its requests take</h2>
        <div className="card card--tight">
          <Distribution
            buckets={detail.distribution}
            total={summary.count}
            slowest={summary.slowest}
            unit="requests"
          />
        </div>
      </div>

      <div className="section">
        <h2 className="section__title">Where its time goes</h2>
        <div className="card card--tight">
          {spans.length === 0 ? (
            <p className="logs__empty">
              Nothing inside this request is instrumented, so this shows how long it took and not
              where the time went.
            </p>
          ) : (
            <RankChart
              rows={spans.map((span) => ({
                label: span.description || span.op,
                // The share is the number that says whether fixing this span would move the
                // endpoint at all — a 2ms query at 40% of the time matters more than a 200ms
                // one at 3%.
                sublabel: `${span.op} · ${span.count.toLocaleString()} calls · ${Math.round(span.share * 100)}% of this endpoint`,
                value: span.total_ms,
                href:
                  `/projects/${id}/span?period=${period}` +
                  `&op=${encodeURIComponent(span.op)}` +
                  `&description=${encodeURIComponent(span.description)}`,
              }))}
              format={formatMs}
              limit={spans.length}
              caption={`Top ${spans.length} operations by time spent inside this endpoint`}
            />
          )}
        </div>
      </div>

      <div className="section">
        <h2 className="section__title">Requests to open · slowest first</h2>
        <div className="card">
          {samples.map((sample) => (
            <Link
              className="mini-row"
              key={sample.transaction_id}
              to={`/projects/${id}/traces/${sample.transaction_id}`}
            >
              <span className="mini-row__main">
                <span className={`level level--${sample.status === 'ok' ? 'info' : 'error'}`}>
                  {sample.status}
                </span>
                <span className="mini-row__title mono">{absoluteTime(sample.timestamp)}</span>
              </span>
              <span className="mini-row__num">
                {formatMs(sample.duration_ms)}
                <em>{relativeTime(sample.timestamp)}</em>
              </span>
            </Link>
          ))}
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
