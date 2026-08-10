import { useState } from 'react'

interface Props {
  /** One count per hour, oldest first. */
  hourly: number[]
  compact?: boolean
}

const GAP = 2 // surface gap between bars, per the mark spec
const RADIUS = 2

/**
 * Events per hour over the last 24 hours.
 *
 * One series, so no legend — the surrounding heading names it. Bars are anchored to the
 * baseline with rounded tops only; a rounded bottom would lift the mark off its own zero line
 * and misstate the value.
 */
export function EventChart({ hourly, compact = false }: Props) {
  const [hover, setHover] = useState<number | null>(null)

  const height = compact ? 28 : 120
  const width = compact ? 110 : 640
  const max = Math.max(...hourly, 1)
  const barWidth = (width - GAP * (hourly.length - 1)) / hourly.length

  return (
    <div className={compact ? 'chart chart--compact' : 'chart'}>
      <svg
        width="100%"
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`Events per hour for the last 24 hours, ${hourly.reduce((a, b) => a + b, 0)} total`}
      >
        {hourly.map((count, index) => {
          // Zero must still be visible as a baseline tick, or an empty hour is indistinguishable
          // from an hour that never rendered.
          const barHeight = count === 0 ? 1 : Math.max(2, (count / max) * (height - 2))
          const x = index * (barWidth + GAP)

          return (
            <g key={index}>
              <rect
                x={x}
                y={height - barHeight}
                width={barWidth}
                height={barHeight}
                rx={count === 0 ? 0 : RADIUS}
                className={count === 0 ? 'chart__bar chart__bar--empty' : 'chart__bar'}
              />
              {/* A full-height hit target: a 3px bar is impossible to hover deliberately. */}
              <rect
                x={x}
                y={0}
                width={barWidth + GAP}
                height={height}
                fill="transparent"
                onMouseEnter={() => setHover(index)}
                onMouseLeave={() => setHover(null)}
              />
            </g>
          )
        })}
      </svg>
      {hover !== null && (
        <div className="chart__tooltip" role="status">
          <strong>{hourly[hover]}</strong> {hourly[hover] === 1 ? 'event' : 'events'}
          <span className="chart__tooltip-when">{hoursAgo(hourly.length - 1 - hover)}</span>
        </div>
      )}
    </div>
  )
}

function hoursAgo(offset: number): string {
  if (offset === 0) return 'this hour'
  if (offset === 1) return '1 hour ago'
  return `${offset} hours ago`
}
