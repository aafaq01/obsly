import { useEffect, useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'

import { api, type Release } from '../api'
import { Notice, Skeleton } from '../components/Notice'
import { PeriodPicker } from '../components/PeriodPicker'
import { handle } from '../errors'
import { formatMs } from '../format'
import { periodLabel } from '../periods'
import { relativeTime } from '../time'

/**
 * Release health.
 *
 * The question a deploy raises and nothing else here answers: did the thing we just shipped
 * make it worse? Every version is a row, so a bad deploy sits next to the good one it replaced
 * rather than being averaged into it.
 *
 * "Failure-free requests", not "crash-free sessions". Sentry's crash-free rate is measured over
 * sessions, which need their own protocol; a request-based number under that name would be
 * compared against other tools and be wrong every time.
 */
export function Releases() {
  const { projectId } = useParams()
  const id = Number(projectId)

  const [params, setParams] = useSearchParams()
  const period = params.get('period') ?? '24h'
  const [releases, setReleases] = useState<Release[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .releases(id, period)
      .then((next) => {
        if (!cancelled) setReleases(next)
      })
      .catch(handle(setError))
    return () => {
      cancelled = true
    }
  }, [id, period])

  if (error) return <Notice>{error}</Notice>
  if (!releases) return <Skeleton rows={4} />

  return (
    <>
      <div className="page-head">
        <h1>Releases</h1>
        <PeriodPicker
          value={period}
          onChange={(next) => {
            const updated = new URLSearchParams(params)
            updated.set('period', next)
            setParams(updated)
          }}
        />
      </div>

      {releases.length === 0 ? (
        <div className="card card--tight">
          <p className="logs__empty">
            No tagged traffic in the last {periodLabel(period)}. Pass <code>release</code> to the
            SDK&rsquo;s <code>init</code> and every signal it sends will be attributed to a version.
          </p>
        </div>
      ) : (
        <>
          <p className="page-head__sub">
            Newest first. <strong>Introduced</strong> counts issues seen for the first time in that
            version — the number that says whether to roll back, as opposed to how much is broken
            overall.
          </p>

          <div className="card">
            <table className="table">
              <thead>
                <tr>
                  <th>Version</th>
                  <th className="num">Requests</th>
                  <th className="num">Adoption</th>
                  <th className="num">Failure-free</th>
                  <th className="num">p95</th>
                  <th className="num">Errors</th>
                  <th className="num">Introduced</th>
                  <th className="num">Last seen</th>
                </tr>
              </thead>
              <tbody>
                {releases.map((release) => (
                  <tr key={release.version}>
                    <td className="mono">{release.version}</td>
                    <td className="num">{release.requests.toLocaleString()}</td>
                    <td className="num">{(release.adoption * 100).toFixed(1)}%</td>
                    <td className={`num strong ${health(release.failure_free_rate)}`}>
                      {(release.failure_free_rate * 100).toFixed(2)}%
                    </td>
                    <td className="num">{formatMs(release.p95)}</td>
                    <td className="num">{release.errors.toLocaleString()}</td>
                    <td className="num">
                      {release.issues_introduced > 0 ? (
                        <strong>{release.issues_introduced}</strong>
                      ) : (
                        '—'
                      )}
                      {release.issues_unresolved > 0 && (
                        <em className="chart2__ago"> {release.issues_unresolved} open</em>
                      )}
                    </td>
                    <td className="num">{relativeTime(release.last_seen)} ago</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="chart2__caption">
            Adoption is share of traffic in this window, not installed base — without session
            reporting there is no honest way to say how many users are on a version, and how much of
            the traffic it served is a fact we do have.
          </p>
        </>
      )}
    </>
  )
}

/** Bands, not a gradient: a deploy is either fine, worth watching, or worth reverting. */
function health(rate: number): string {
  if (rate >= 0.995) return 'health--good'
  return rate >= 0.98 ? 'health--warn' : 'health--bad'
}
