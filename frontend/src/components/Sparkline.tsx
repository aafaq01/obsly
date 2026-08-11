import { useState } from 'react'

import { agoLabel, humanSpan } from '../format'
import { bucketTime, clockLabel } from '../time'

interface Props {
  values: number[]
  /** Rendered in the readout, e.g. "ms" or "req". */
  unit?: string
  /** Formats one value for the readout. Defaults to a locale integer. */
  format?: (value: number) => string
  /** Status colouring for series where "up" is bad — failures, errors. */
  tone?: 'series' | 'critical'
  /** What one point covers, so the axis can say so in the right unit. */
  bucketSeconds?: number
  /** ISO time of the first point, so the axis can read in clock time. */
  startedAt?: string
}

const HEIGHT = 56
const WIDTH = 240

/**
 * A single-series trend line.
 *
 * A line rather than bars because these are rates sampled over time, where the shape between
 * points is the information — bars imply each bucket is an independent quantity.
 *
 * One series per chart, always. Two y-scales on one plot invent a correlation that is not in
 * the data, which is the most common way a dashboard misleads.
 */
export function Sparkline({
  values,
  unit = '',
  format,
  tone = 'series',
  bucketSeconds = 3600,
  startedAt,
}: Props) {
  const [hover, setHover] = useState<number | null>(null)

  const max = Math.max(...values, 1)
  const stamp = (index: number) => {
    const moment = bucketTime(startedAt, index, bucketSeconds)
    return moment ? clockLabel(moment, bucketSeconds) : null
  }
  const render = format ?? ((value: number) => Math.round(value).toLocaleString())
  const shown = hover === null ? values[values.length - 1] : values[hover]

  const step = values.length > 1 ? WIDTH / (values.length - 1) : WIDTH
  const points = values
    .map((value, index) => `${index * step},${HEIGHT - (value / max) * (HEIGHT - 4) - 2}`)
    .join(' ')

  return (
    <div className="spark">
      <div className="spark__readout">
        <strong>{render(shown ?? 0)}</strong>
        {unit && <span className="spark__unit">{unit}</span>}
        <span className="spark__when">
          {hover === null
            ? (stamp(values.length - 1) ?? 'latest')
            : (stamp(hover) ?? agoLabel((values.length - 1 - hover) * bucketSeconds))}
        </span>
      </div>

      <svg
        className={`spark__svg spark__svg--${tone}`}
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`Trend over the last ${humanSpan(values.length * bucketSeconds)}, peak ${render(max)}${unit}`}
        onMouseLeave={() => setHover(null)}
      >
        <polyline className="spark__line" points={points} />
        {/* Full-height columns, because a 2px line is impossible to hover deliberately. */}
        {values.map((_, index) => (
          <rect
            key={index}
            x={index * step - step / 2}
            y={0}
            width={step}
            height={HEIGHT}
            fill="transparent"
            onMouseEnter={() => setHover(index)}
          />
        ))}
        {hover !== null && (
          <circle
            className="spark__dot"
            cx={hover * step}
            cy={HEIGHT - ((values[hover] ?? 0) / max) * (HEIGHT - 4) - 2}
            r={3}
          />
        )}
      </svg>

      <div className="spark__axis">
        <span>{stamp(0) ?? `${humanSpan(values.length * bucketSeconds)} ago`}</span>
        <span>{stamp(values.length - 1) ?? 'now'}</span>
      </div>
    </div>
  )
}
