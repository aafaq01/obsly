import { useState } from 'react'

import { agoLabel, bucketLabel, humanSpan } from '../format'
import { bucketTime, clockLabel } from '../time'

interface Props {
  /** One count per bucket, oldest first. */
  hourly: number[]
  /** Row-sized: bars only, no axis or readout. */
  compact?: boolean
  /** What one bucket covers, for the axis and the table view. */
  bucketSeconds?: number
  unit?: string
  /** ISO time of the first bucket. With it the axis reads in clock time; without it the
   *  chart falls back to relative ages, which is all it could say before. */
  startedAt?: string
  /** What the chart measures, stated rather than left to be inferred from context. */
  caption?: string
}

/**
 * Counts per hour over a window.
 *
 * Built from positioned elements rather than a scaled SVG viewBox: `preserveAspectRatio="none"`
 * stretches the geometry horizontally, so a 2px corner radius renders as a visibly squashed
 * ellipse at full width.
 *
 * The value is readable three ways — the axis maximum, the hover readout, and a table view —
 * because a tooltip that is the only route to a number is unreachable by keyboard and gone
 * the moment you look away.
 */
export function EventChart({
  hourly,
  compact = false,
  bucketSeconds = 3600,
  unit = 'events',
  startedAt,
  caption,
}: Props) {
  const [hover, setHover] = useState<number | null>(null)
  const [showTable, setShowTable] = useState(false)

  const max = Math.max(...hourly, 1)
  const total = hourly.reduce((sum, value) => sum + value, 0)
  const spanSeconds = hourly.length * bucketSeconds
  const stamp = (index: number) => {
    const moment = bucketTime(startedAt, index, bucketSeconds)
    return moment ? clockLabel(moment, bucketSeconds) : null
  }
  const ago = (index: number) => agoLabel((hourly.length - 1 - index) * bucketSeconds)

  const bars = (
    <div
      className={compact ? 'bars bars--compact' : 'bars'}
      role="img"
      aria-label={`${unit} per ${bucketLabel(bucketSeconds)} over the last ${humanSpan(spanSeconds)}, ${total} total`}
    >
      {hourly.map((count, index) => (
        <div
          key={index}
          className="bars__slot"
          onMouseEnter={() => setHover(index)}
          onMouseLeave={() => setHover(null)}
        >
          <div
            className={count === 0 ? 'bars__bar bars__bar--empty' : 'bars__bar'}
            // A zero bucket still draws a baseline tick: "no events this hour" and "this
            // chart failed to render" must not look the same.
            style={{ height: count === 0 ? '1px' : `${Math.max(2, (count / max) * 100)}%` }}
          />
        </div>
      ))}
    </div>
  )

  if (compact) return bars

  return (
    <figure className="chart2">
      {/* Reserved row, not an overlay. A tooltip positioned above the plot is clipped by the
          card that contains it — which is exactly what it did before. */}
      <figcaption className="chart2__readout">
        {hover === null ? (
          <>
            <strong>{total.toLocaleString()}</strong> {unit} · peak {max.toLocaleString()} per{' '}
            {bucketLabel(bucketSeconds)}
          </>
        ) : (
          <>
            <strong>{hourly[hover]?.toLocaleString()}</strong> {unit} · {stamp(hover) ?? ago(hover)}
            {stamp(hover) && <span className="chart2__ago"> · {ago(hover)}</span>}
          </>
        )}
      </figcaption>

      <div className="chart2__plot">
        <span className="chart2__ymax">{max.toLocaleString()}</span>
        {bars}
      </div>

      <div className="chart2__xaxis">
        <span>{stamp(0) ?? `${humanSpan(spanSeconds)} ago`}</span>
        <span>
          {stamp(Math.floor((hourly.length - 1) / 2)) ??
            `${humanSpan(Math.round(spanSeconds / 2))} ago`}
        </span>
        <span>{stamp(hourly.length - 1) ?? 'now'}</span>
      </div>

      {caption && <p className="chart2__caption">{caption}</p>}

      <button className="chart2__toggle" onClick={() => setShowTable(!showTable)}>
        {showTable ? 'Hide values' : 'Show values as a table'}
      </button>

      {showTable && (
        <table className="chart2__table">
          <thead>
            <tr>
              <th>When</th>
              <th className="num">{unit}</th>
            </tr>
          </thead>
          <tbody>
            {hourly
              .map((count, index) => ({ count, index }))
              .filter(({ count }) => count > 0)
              .reverse()
              .map(({ count, index }) => (
                <tr key={index}>
                  <td>
                    {stamp(index) ?? ago(index)}
                    {stamp(index) && <em className="chart2__ago"> {ago(index)}</em>}
                  </td>
                  <td className="num">{count.toLocaleString()}</td>
                </tr>
              ))}
          </tbody>
        </table>
      )}
    </figure>
  )
}
