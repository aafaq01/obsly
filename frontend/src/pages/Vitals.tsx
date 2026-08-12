import { useEffect, useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'

import { api, type WebVitals } from '../api'
import { Notice, Skeleton } from '../components/Notice'
import { handle } from '../errors'
import { formatMs } from '../format'
import { periodLabel } from '../periods'

/**
 * Core Web Vitals.
 *
 * Every number is a p75 against the standard's own thresholds, because these are the figures a
 * team gets judged on publicly and a tool that invents its own bands cannot be compared with
 * anything else anyone reads.
 *
 * A vital nobody reported shows as "no data" rather than as a passing score. Green because
 * nothing was measured is the most dangerous reading a page like this can give.
 */
export function Vitals() {
  const { projectId } = useParams()
  const id = Number(projectId)

  const [params] = useSearchParams()
  const period = params.get('period') ?? '24h'
  const [data, setData] = useState<WebVitals | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .vitals(id, period)
      .then((next) => {
        if (!cancelled) setData(next)
      })
      .catch(handle(setError))
    return () => {
      cancelled = true
    }
  }, [id, period])

  if (error) return <Notice>{error}</Notice>
  if (!data) return <Skeleton rows={4} />

  return (
    <>
      <div className="page-head">
        <h1>Web Vitals</h1>
      </div>

      <p className="page-head__sub">
        {data.pageloads.toLocaleString()} page loads in the last {periodLabel(period)}. Each score
        is the 75th percentile — three quarters of visits were at least this good.
      </p>

      {data.pageloads === 0 ? (
        <div className="card card--tight">
          <p className="logs__empty">
            No page loads reported yet. These come from the browser SDK — add{' '}
            <code>@obsly/browser</code> to the frontend and they will appear here.
          </p>
        </div>
      ) : (
        <>
          <div className="vitals">
            {data.vitals.map((vital) => (
              <VitalCard key={vital.key} vital={vital} />
            ))}
          </div>

          <div className="section">
            <h2 className="section__title">Pages worth opening</h2>
            <p className="chart2__caption">
              Ranked by p75 LCP, not by traffic — a list ordered by traffic only repeats the traffic
              report.
            </p>
            <div className="card">
              <table className="table">
                <thead>
                  <tr>
                    <th>Page</th>
                    <th className="num">Loads</th>
                    <th className="num">LCP</th>
                    <th className="num">CLS</th>
                    <th className="num">INP</th>
                  </tr>
                </thead>
                <tbody>
                  {data.pages.map((page) => (
                    <tr key={page.name}>
                      <td className="mono">
                        <span className={`dot dot--${page.rating}`} aria-hidden="true" />
                        {page.name}
                      </td>
                      <td className="num">{page.count.toLocaleString()}</td>
                      <td className="num strong">{page.lcp === null ? '—' : formatMs(page.lcp)}</td>
                      <td className="num">{page.cls === null ? '—' : page.cls.toFixed(3)}</td>
                      <td className="num">{page.inp === null ? '—' : formatMs(page.inp)}</td>
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

function VitalCard({ vital }: { vital: WebVitals['vitals'][number] }) {
  const shown =
    vital.value === null ? '—' : vital.unit === '' ? vital.value.toFixed(3) : formatMs(vital.value)

  return (
    <div className={`vital vital--${vital.rating}`}>
      <div className="vital__head">
        <span className="vital__label">{vital.label}</span>
        <span className={`tag tag--${vital.rating}`}>
          {vital.rating === 'none' ? 'no data' : vital.rating}
        </span>
      </div>

      <div className="vital__value mono">{shown}</div>

      <p className="vital__explains">{vital.explains}</p>

      {/* The bands stated, not implied by the colour. A reader who does not already know the
          thresholds cannot act on a green box. */}
      <p className="vital__bands">
        good ≤ {format(vital.good_below, vital.unit)} · poor &gt;{' '}
        {format(vital.poor_above, vital.unit)}
        {vital.value !== null && ` · ${vital.samples.toLocaleString()} samples`}
      </p>
    </div>
  )
}

function format(value: number, unit: string): string {
  return unit === '' ? value.toFixed(2) : formatMs(value)
}
