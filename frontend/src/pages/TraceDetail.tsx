import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { api, type TraceDetail as Detail } from '../api'
import { LogList } from '../components/LogList'
import { Notice } from '../components/Notice'
import { handle } from '../errors'
import { absoluteTime } from '../time'
import { formatMs } from '../format'

export function TraceDetail() {
  const { traceId } = useParams()
  const [trace, setTrace] = useState<Detail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!traceId) return
    api.trace(traceId).then(setTrace).catch(handle(setError))
  }, [traceId])

  if (error) return <Notice>{error}</Notice>
  if (!trace) return <Notice>Loading trace…</Notice>

  const start = new Date(trace.start_timestamp).getTime()
  const total = trace.duration_ms || 1

  return (
    <>
      <h1 className="detail-title">{trace.name}</h1>
      <div className="detail-culprit">
        {trace.op} · trace {trace.trace_id.slice(0, 16)}…
      </div>

      <div className="stat-row">
        <Stat label="Duration" value={formatMs(trace.duration_ms)} />
        <Stat label="Status" value={trace.status} />
        <Stat label="Spans" value={String(trace.span_count)} />
        <Stat label="Release" value={trace.release || '—'} />
        <Stat label="Started" value={absoluteTime(trace.start_timestamp)} />
      </div>

      {trace.logs.length > 0 && (
        <div className="section">
          <h2 className="section__title">Logs from this request</h2>
          <div className="card">
            <LogList records={trace.logs} />
          </div>
        </div>
      )}

      {trace.errors.length > 0 && (
        <div className="section">
          <h2 className="section__title">Errors in this request</h2>
          <div className="card">
            {trace.errors.map((error) =>
              error.issue_id ? (
                <Link to={`/issues/${error.issue_id}`} className="correlate-row" key={error.id}>
                  <span className={`level level--${error.level}`}>{error.level}</span>
                  <span className="correlate-row__title">{error.title}</span>
                  <span className="correlate-row__go">View issue →</span>
                </Link>
              ) : (
                <div className="correlate-row" key={error.id}>
                  <span className={`level level--${error.level}`}>{error.level}</span>
                  <span className="correlate-row__title">{error.title}</span>
                </div>
              ),
            )}
          </div>
        </div>
      )}

      <div className="section">
        <h2 className="section__title">Waterfall</h2>
        <div className="card" style={{ padding: '4px 0' }}>
          <WaterfallRow
            label={trace.name}
            op={trace.op}
            duration={trace.duration_ms}
            offset={0}
            total={total}
            depth={0}
            root
          />
          {trace.spans.map((span) => (
            <WaterfallRow
              key={span.span_id}
              label={span.description || span.op}
              op={span.op}
              duration={span.duration_ms}
              offset={new Date(span.start_timestamp).getTime() - start}
              total={total}
              depth={span.parent_span_id === trace.span_id ? 1 : 2}
            />
          ))}
          {trace.spans.length === 0 && (
            <div style={{ padding: '14px 16px' }}>
              <Notice>
                <strong>No spans inside this request</strong>
                The request itself is timed, but nothing inside it is instrumented yet — so this
                shows how long it took, not where the time went. Wrap work in{' '}
                <code>obsly.start_span()</code>, or wait for automatic database instrumentation.
              </Notice>
            </div>
          )}
        </div>
      </div>
    </>
  )
}

interface RowProps {
  label: string
  op: string
  duration: number
  offset: number
  total: number
  depth: number
  root?: boolean
}

/**
 * One span. The bar's left edge is when it started relative to the request, its width is how
 * long it ran — so a gap between two bars is real dead time, not a layout artefact.
 */
function WaterfallRow({ label, op, duration, offset, total, depth, root }: RowProps) {
  const left = Math.max(0, Math.min(100, (offset / total) * 100))
  // Floored at 0.5% so a sub-millisecond span is still a visible mark rather than nothing.
  const width = Math.max(0.5, Math.min(100 - left, (duration / total) * 100))

  return (
    <div className={root ? 'wf wf--root' : 'wf'}>
      <div className="wf__label" style={{ paddingLeft: 16 + depth * 14 }}>
        <span className="wf__op">{op}</span>
        <span className="wf__desc" title={label}>
          {label}
        </span>
      </div>
      <div className="wf__track">
        <div className="wf__bar" style={{ left: `${left}%`, width: `${width}%` }} />
      </div>
      <div className="wf__ms">{formatMs(duration)}</div>
    </div>
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
