import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { api, type ExceptionValue, type Frame, type IssueDetail } from '../api'
import { EventChart } from '../components/EventChart'
import { absoluteTime, relativeTime } from '../time'
import { Notice } from '../components/Notice'
import { handle } from '../errors'

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

  const { issue, latest_event: event, tags } = detail

  return (
    <>
      <p style={{ marginBottom: 12 }}>
        <Link to={`/projects/${issue.project}/issues`} style={{ color: 'var(--series-1)' }}>
          ← Back to issues
        </Link>
      </p>

      <div className="detail-header">
        <h1 className="detail-title">{issue.title}</h1>
        {issue.culprit && <div className="detail-culprit">{issue.culprit}</div>}
      </div>

      <div className="stat-row">
        <Stat label="Events" value={issue.times_seen.toLocaleString()} />
        <Stat label="Level" value={issue.level} />
        <Stat label="Status" value={issue.status} />
        <Stat label="First seen" value={relativeTime(issue.first_seen)} />
        <Stat label="Last seen" value={relativeTime(issue.last_seen)} />
      </div>

      <div className="section">
        <h2 className="section__title">Events per hour · last 24 hours</h2>
        <div className="card" style={{ padding: 16 }}>
          <EventChart hourly={issue.hourly} />
        </div>
      </div>

      <div className="grid-2 section">
        <div>
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

          {event && (
            <div className="section">
              <h2 className="section__title">Raw payload</h2>
              <pre className="raw">{JSON.stringify(event.payload, null, 2)}</pre>
            </div>
          )}
        </div>

        <div>
          <h2 className="section__title">Event detail</h2>
          <div className="card" style={{ padding: 14 }}>
            <dl className="kv">
              <dt>Event ID</dt>
              <dd>{event?.id ?? '—'}</dd>
              <dt>When</dt>
              <dd>{event ? absoluteTime(event.timestamp) : '—'}</dd>
              <dt>Release</dt>
              <dd>{event?.release || '—'}</dd>
              <dt>Environment</dt>
              <dd>{event?.environment || '—'}</dd>
              <dt>Server</dt>
              <dd>{event?.server_name || '—'}</dd>
              <dt>Platform</dt>
              <dd>{event?.platform || '—'}</dd>
            </dl>
          </div>

          {Object.keys(tags).length > 0 && (
            <div className="section">
              <h2 className="section__title">Tags</h2>
              <div className="card" style={{ padding: 14 }}>
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
            </div>
          )}

          <div className="section">
            <h2 className="section__title">Why these grouped</h2>
            <div className="card" style={{ padding: 14 }}>
              <div className="frames">
                {issue.fingerprint_components.map((component, index) => (
                  <div className="frame" key={index}>
                    {component}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
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

function ExceptionBlock({ value, isLast }: { value: ExceptionValue; isLast: boolean }) {
  const inApp = value.frames.filter((frame) => frame.in_app)
  // Collapsed by default: the whole reason in_app exists is that a 41-frame trace with two
  // frames of your code in the middle is unreadable.
  const [showAll, setShowAll] = useState(inApp.length === 0)
  const shown = showAll ? value.frames : inApp
  const hidden = value.frames.length - shown.length

  return (
    <div style={{ marginBottom: 16 }}>
      <p style={{ margin: '0 0 8px', fontWeight: 600 }}>
        {value.type}
        {value.value && <span style={{ fontWeight: 400 }}>: {value.value}</span>}
        {!isLast && (
          <span style={{ color: 'var(--ink-muted)', fontWeight: 400 }}> — caused the next</span>
        )}
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
      <span>
        {frame.module || frame.filename} in <strong>{frame.function}</strong>
      </span>
    </div>
  )
}
