import { useEffect, useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'

import { api, type SpanStats } from '../api'
import { Notice, Skeleton } from '../components/Notice'
import { SetupPrompt } from '../components/SetupPrompt'
import { RankChart } from '../components/RankChart'
import { handle } from '../errors'
import { formatMs } from '../format'

/**
 * The cache tier.
 *
 * A cache is the one layer where being fast is not the point — a cache that answers in 0.2ms
 * and misses every time is slower overall than no cache at all, because every miss still pays
 * for the lookup before paying for the query behind it. So the page leads with how much work
 * the cache is doing, and is explicit that hit rate is the number that decides whether it is
 * worth having.
 */
export function Cache() {
  const { projectId } = useParams()
  const id = Number(projectId)
  const [params] = useSearchParams()
  const period = params.get('period') ?? '24h'

  const [spans, setSpans] = useState<SpanStats[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .spans(id, period, '')
      .then((data) => {
        if (!cancelled) setSpans(data.spans.filter((span) => span.op.startsWith('cache.')))
      })
      .catch(handle(setError))
    return () => {
      cancelled = true
    }
  }, [id, period])

  if (error) return <Notice>{error}</Notice>
  if (!spans) return <Skeleton rows={4} />

  const operations = spans.reduce((sum, span) => sum + span.count, 0)
  const totalMs = spans.reduce((sum, span) => sum + span.total_ms, 0)

  return (
    <>
      <div className="page-head">
        <h1>Cache</h1>
      </div>

      {spans.length === 0 ? (
        <SetupPrompt tier="backend">
          Cache lookups are recorded by the backend SDK. Wrap one in{' '}
          <code>obsly.start_span(&quot;cache.get&quot;, key)</code> and it will appear here.
        </SetupPrompt>
      ) : (
        <>
          <dl className="meta">
            <Meta label="Operations" value={operations.toLocaleString()} />
            <Meta label="Time in cache" value={formatMs(totalMs)} />
            <Meta label="Distinct keys" value={String(spans.length)} />
            <Meta
              label="Slowest p95"
              value={formatMs(Math.max(...spans.map((span) => span.p95)))}
            />
          </dl>

          {/* Stated, not hidden. A cache page with no hit rate looks like a cache that is
              working; it is a cache nobody measured. */}
          <div className="notice notice--inline">
            Hit rate is not being reported. Set <code>cache.hit</code> in the span data and this
            page can tell you whether the cache is earning its place — a cache that answers in 0.2ms
            and misses every time costs more than no cache at all.
          </div>

          <div className="section">
            <h2 className="section__title">Where the cache spends its time</h2>
            <div className="card card--tight">
              <RankChart
                rows={spans.map((span) => ({
                  label: span.description || span.op,
                  sublabel: `${span.op} · ${span.count.toLocaleString()} calls · ${span.per_transaction}× per request`,
                  value: span.total_ms,
                  href: `/projects/${id}/span?period=${period}&op=${encodeURIComponent(span.op)}&description=${encodeURIComponent(span.description)}`,
                }))}
                format={formatMs}
                limit={spans.length}
                caption="Bar length is total time spent in this cache operation, relative to the highest"
              />
            </div>
          </div>

          <div className="section">
            <h2 className="section__title">Operations</h2>
            <div className="card">
              <table className="table">
                <thead>
                  <tr>
                    <th>Key</th>
                    <th>Operation</th>
                    <th className="num">Calls</th>
                    <th className="num">Per request</th>
                    <th className="num">p50</th>
                    <th className="num strong">p95</th>
                    <th className="num">Total</th>
                  </tr>
                </thead>
                <tbody>
                  {spans.map((span) => (
                    <tr key={`${span.op}:${span.description}`}>
                      <td className="mono">{span.description || '—'}</td>
                      <td className="mono">{span.op}</td>
                      <td className="num">{span.count.toLocaleString()}</td>
                      <td className="num">{span.per_transaction}×</td>
                      <td className="num">{formatMs(span.p50)}</td>
                      <td className="num strong">{formatMs(span.p95)}</td>
                      <td className="num">{formatMs(span.total_ms)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
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
