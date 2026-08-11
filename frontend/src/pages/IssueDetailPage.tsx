import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { api, type ExceptionValue, type Frame, type IssueDetail } from '../api'
import { Breadcrumbs } from '../components/Breadcrumbs'
import { EventChart } from '../components/EventChart'
import { Notice } from '../components/Notice'
import { StatusActions } from '../components/StatusActions'
import { handle } from '../errors'
import { absoluteTime, relativeTime } from '../time'

export function IssueDetailPage() {
  const { issueId } = useParams()
  const [detail, setDetail] = useState<IssueDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!issueId) return
    api.issue(Number(issueId)).then(setDetail).catch(handle(setError))
  }, [issueId])

  if (error) return <Notice>{error}</Notice>
  if (!detail) return <Notice>Loading issue…</Notice>

  const { issue, latest_event: event, tags, trace } = detail

  return (
    <>
      <Breadcrumbs
        trail={[
          { label: 'Issues', to: `/projects/${issue.project}/issues` },
          { label: issue.title },
        ]}
      />

      <div className="detail-header">
        <h1 className="detail-title">{issue.title}</h1>
        {issue.culprit && <div className="detail-culprit">{issue.culprit}</div>}
        <StatusActions
          issueId={issue.id}
          status={issue.status}
          onChange={(status) => setDetail({ ...detail, issue: { ...issue, status } })}
        />
      </div>

      {/* One dense line rather than a row of oversized tiles. These are reference values you
          glance at; the stack trace is the headline of the page. */}
      <dl className="meta">
        <Meta label="Events" value={issue.times_seen.toLocaleString()} />
        <Meta label="Level" value={issue.level} />
        <Meta label="First seen" value={relativeTime(issue.first_seen)} />
        <Meta label="Last seen" value={relativeTime(issue.last_seen)} />
        {event?.release && <Meta label="Release" value={event.release} />}
        {event?.environment && <Meta label="Environment" value={event.environment} />}
      </dl>

      {trace && (
        <Link to={`/projects/${issue.project}/traces/${trace.id}`} className="correlate">
          <span className="correlate__label">Happened inside</span>
          <code>{trace.name}</code>
          <span className="correlate__meta">
            {Math.round(trace.duration_ms)}ms · {trace.status}
          </span>
          <span className="correlate__go">View trace →</span>
        </Link>
      )}

      {issue.category === 'performance' && 'description' in issue.evidence && (
        <div className="evidence">
          <h2 className="section__title">What the detector found</h2>
          <div className="card card--tight">
            <code className="evidence__query">{issue.evidence.description}</code>
            <dl className="evidence__stats">
              <div>
                <dt>Repeats per request</dt>
                <dd>{issue.evidence.repeat_count}</dd>
              </div>
              <div>
                <dt>Total time</dt>
                <dd>{Math.round(issue.evidence.total_ms)}ms</dd>
              </div>
              <div>
                {/* One of those queries is legitimate; the rest are the bug. */}
                <dt>Recoverable</dt>
                <dd className="evidence__win">{Math.round(issue.evidence.wasted_ms)}ms</dd>
              </div>
              <div>
                <dt>Endpoint</dt>
                <dd>{issue.evidence.transaction}</dd>
              </div>
            </dl>
          </div>
        </div>
      )}

      <div className="issue-grid">
        <div className="issue-grid__main">
          <section>
            <h2 className="section__title">Stack trace · most recent event</h2>
            {event && event.exception.length > 0 ? (
              event.exception.map((value, index) => (
                <ExceptionBlock
                  key={index}
                  value={value}
                  isLast={index === event.exception.length - 1}
                />
              ))
            ) : (
              <Notice>{event?.message || 'This event carried no stack trace.'}</Notice>
            )}
          </section>

          {/* Collapsed. The payload is a debugging escape hatch, not the page — open it was
              two screens of JSON pushing the stack trace out of view. */}
          {event && (
            <details className="collapse">
              <summary>Raw payload</summary>
              <pre className="raw">{JSON.stringify(event.payload, null, 2)}</pre>
            </details>
          )}
        </div>

        <aside className="issue-grid__side">
          <section>
            <h2 className="section__title">When this happened</h2>
            <div className="card card--tight">
              <EventChart
                hourly={issue.hourly}
                bucketSeconds={issue.bucket_seconds}
                startedAt={issue.hourly_start}
                caption="Each bar is one bucket of captured events. A cluster says the bug is tied to something that happened; a flat line says it is always on."
              />
            </div>
          </section>

          <section>
            <h2 className="section__title">Latest event</h2>
            <div className="card card--tight">
              <dl className="kv">
                <dt>Event ID</dt>
                <dd>{event ? `${event.id.slice(0, 18)}…` : '—'}</dd>
                <dt>When</dt>
                <dd>{event ? absoluteTime(event.timestamp) : '—'}</dd>
                <dt>Server</dt>
                <dd>{event?.server_name || '—'}</dd>
                <dt>Platform</dt>
                <dd>{event?.platform || '—'}</dd>
              </dl>
            </div>
          </section>

          {Object.keys(tags).length > 0 && (
            <section>
              <h2 className="section__title">Tags</h2>
              <div className="card card--tight">
                {Object.entries(tags).map(([key, values]) => (
                  <div className="tag-group" key={key}>
                    <div className="tag-group__key">{key}</div>
                    {values.map((tag) => (
                      <div key={tag.value}>
                        <div className="tag-row">
                          <span>{tag.value}</span>
                          <span>{tag.percentage}%</span>
                        </div>
                        <div className="tag-bar" style={{ width: `${tag.percentage}%` }} />
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            </section>
          )}

          <details className="collapse">
            <summary>Why these grouped</summary>
            <div className="frames">
              {issue.fingerprint_components.map((component, index) => (
                <div className="frame" key={index}>
                  {component}
                </div>
              ))}
            </div>
          </details>
        </aside>
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

function ExceptionBlock({ value, isLast }: { value: ExceptionValue; isLast: boolean }) {
  const inApp = value.frames.filter((frame) => frame.in_app)
  // Collapsed by default: the whole reason in_app is computed is that a 41-frame trace with
  // three frames of your code buried in it is unreadable.
  const [showAll, setShowAll] = useState(inApp.length === 0)
  const shown = showAll ? value.frames : inApp
  const hidden = value.frames.length - shown.length

  return (
    <div className="exc">
      <p className="exc__head">
        <span className="exc__type">{value.type}</span>
        {value.value && <span className="exc__value">{value.value}</span>}
        {!isLast && <span className="exc__chain">caused the next</span>}
      </p>
      <div className="card">
        {hidden > 0 && (
          <button className="frames__toggle" onClick={() => setShowAll(!showAll)}>
            {showAll ? `Hide ${hidden} system frames` : `Show ${hidden} more system frames`}
          </button>
        )}
        <div className="frames">
          {shown.map((frame, index) => (
            <FrameRow key={index} frame={frame} />
          ))}
        </div>
      </div>
    </div>
  )
}

function FrameRow({ frame }: { frame: Frame }) {
  return (
    <div className={frame.in_app ? 'frame frame--in-app' : 'frame'}>
      <span className="frame__lineno">{frame.lineno ?? '—'}</span>
      <span className="frame__where">
        {frame.module || frame.filename} in <strong>{frame.function}</strong>
      </span>
    </div>
  )
}
