import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { api, type TraceDetail as Detail, type TraceSpan } from '../api'
import { Breadcrumbs } from '../components/Breadcrumbs'
import { LogList } from '../components/LogList'
import { Notice } from '../components/Notice'
import { handle } from '../errors'
import { formatMs } from '../format'
import { absoluteTime, preciseTime } from '../time'

export function TraceDetail() {
  const { traceId, projectId } = useParams()
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
      <Breadcrumbs
        trail={[{ label: 'Traces', to: `/projects/${projectId}/traces` }, { label: trace.name }]}
      />

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
                <Link
                  to={`/projects/${projectId}/issues/${error.issue_id}`}
                  className="correlate-row"
                  key={error.id}
                >
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
          {group(trace.spans).map((entry) =>
            entry.spans.length === 1 ? (
              <WaterfallRow
                key={entry.spans[0]!.span_id}
                label={entry.spans[0]!.description || entry.spans[0]!.op}
                op={entry.spans[0]!.op}
                duration={entry.spans[0]!.duration_ms}
                offset={new Date(entry.spans[0]!.start_timestamp).getTime() - start}
                total={total}
                depth={entry.spans[0]!.parent_span_id === trace.span_id ? 1 : 2}
              />
            ) : (
              <WaterfallGroup key={entry.key} entry={entry} start={start} total={total} />
            ),
          )}
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

interface Group {
  key: string
  op: string
  label: string
  spans: TraceSpan[]
}

/**
 * Collapse runs of the same span into one row.
 *
 * A request that issues the same query twenty-five times renders as twenty-five near-identical
 * bars, and the interesting spans — the ones that are different — get pushed off the screen by
 * the repetition. The repetition is itself the finding, so it becomes one row that says how
 * many and how much, and opens if you want the individual timings.
 *
 * Only consecutive spans group. Two runs of the same query separated by an HTTP call are two
 * different events in the request's story, and merging them across the thing that sits between
 * them would hide the ordering that explains it.
 */
function group(spans: TraceSpan[]): Group[] {
  const groups: Group[] = []

  for (const span of spans) {
    const label = span.description || span.op
    const last = groups[groups.length - 1]

    // Compared on what the span is, not on the row key. The key carries a position suffix so
    // two separate runs of the same query are distinct rows for React, and comparing against
    // it meant a group never matched itself.
    if (last && last.op === span.op && last.label === label) last.spans.push(span)
    else
      groups.push({
        key: `${span.op}:${label}:${groups.length}`,
        op: span.op,
        label,
        spans: [span],
      })
  }

  return groups
}

function WaterfallGroup({ entry, start, total }: { entry: Group; start: number; total: number }) {
  const [open, setOpen] = useState(false)

  const totalMs = entry.spans.reduce((sum, span) => sum + span.duration_ms, 0)
  const first = new Date(entry.spans[0]!.start_timestamp).getTime()
  const lastSpan = entry.spans[entry.spans.length - 1]!
  const endsAt = new Date(lastSpan.timestamp).getTime()

  const left = Math.max(0, Math.min(100, ((first - start) / total) * 100))
  // The group's bar spans first start to last end, so it shows the stretch of the request
  // these calls occupied — which is the number that says whether they were serialised.
  const width = Math.max(0.5, Math.min(100 - left, ((endsAt - first) / total) * 100))

  return (
    <>
      <div className="wf wf--group">
        {/* The label column stacks op over description, so the toggle and the count belong
            on the op line rather than as two more rows of their own. */}
        <div className="wf__label" style={{ paddingLeft: 16 }}>
          <span className="wf__meta">
            <button
              className="wf__toggle"
              onClick={() => setOpen(!open)}
              aria-expanded={open}
              aria-label={`${open ? 'Hide' : 'Show'} the ${entry.spans.length} individual calls`}
            >
              {open ? '▾' : '▸'}
            </button>
            <span className="wf__count">{`${entry.spans.length}×`}</span>
            <span className="wf__op">{entry.op}</span>
          </span>
          <span className="wf__desc" title={entry.label}>
            {entry.label}
          </span>
        </div>
        <div className="wf__track">
          <div
            className="wf__bar wf__bar--group"
            style={{ left: `${left}%`, width: `${width}%` }}
          />
        </div>
        <div className="wf__ms">{formatMs(totalMs)}</div>
      </div>

      {open &&
        entry.spans.map((span) => {
          const offset = new Date(span.start_timestamp).getTime() - start
          const spanLeft = Math.max(0, Math.min(100, (offset / total) * 100))
          const spanWidth = Math.max(
            0.5,
            Math.min(100 - spanLeft, (span.duration_ms / total) * 100),
          )

          return (
            <div className="wf wf--child" key={span.span_id}>
              <div className="wf__label" style={{ paddingLeft: 52 }}>
                {/* Both, because they answer different questions: the offset says where in the
                    request this call happened, the clock time lines it up against a log line. */}
                <span className="wf__at mono">{`+${formatMs(offset)}`}</span>
                <span className="wf__clock">{preciseTime(span.start_timestamp)}</span>
              </div>
              <div className="wf__track">
                <div className="wf__bar" style={{ left: `${spanLeft}%`, width: `${spanWidth}%` }} />
              </div>
              <div className="wf__ms">{formatMs(span.duration_ms)}</div>
            </div>
          )
        })}
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
