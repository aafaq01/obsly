import { useState } from 'react'

import { formatMs } from '../format'

interface Bucket {
  from_ms: number
  to_ms: number
  count: number
}

interface Props {
  buckets: Bucket[]
  total: number
  slowest: number
  unit: string
}

/**
 * A latency histogram.
 *
 * The x-axis is duration, not clock time — this says how long things take, not when they
 * happened. Two percentiles describe two points; the shape between them says whether this is
 * one slow tail or two behaviours sharing a name, and those need different fixes.
 *
 * The readout is a reserved row rather than a floating tooltip, so it cannot be clipped by the
 * card and is readable without a mouse hovering the right pixel.
 */
export function Distribution({ buckets, total, slowest, unit }: Props) {
  const [hover, setHover] = useState<number | null>(null)

  const peak = buckets.reduce((max, bucket) => Math.max(max, bucket.count), 0)
  const active = hover === null ? null : buckets[hover]

  return (
    <figure className="dist-figure">
      <figcaption className="chart2__readout">
        {active ? (
          <>
            <strong>{active.count.toLocaleString()}</strong> {unit} took {formatMs(active.from_ms)}–
            {formatMs(active.to_ms)}
          </>
        ) : (
          <>
            <strong>{total.toLocaleString()}</strong> {unit} · fastest {formatMs(0)} · slowest{' '}
            {formatMs(slowest)}
          </>
        )}
      </figcaption>

      <div
        className="dist"
        role="img"
        aria-label={`How long ${total} ${unit} took, from 0 to ${formatMs(slowest)}`}
        onMouseLeave={() => setHover(null)}
      >
        {buckets.map((bucket, index) => (
          <div
            className="dist__slot"
            key={index}
            onMouseEnter={() => setHover(index)}
            onFocus={() => setHover(index)}
            tabIndex={bucket.count > 0 ? 0 : -1}
            // Also on the element, so the count is reachable by touch and by keyboard rather
            // than only by a mouse resting on the right pixel.
            title={`${bucket.count} ${unit} took ${formatMs(bucket.from_ms)}–${formatMs(bucket.to_ms)}`}
          >
            <div
              className={bucket.count === 0 ? 'dist__bar dist__bar--empty' : 'dist__bar'}
              style={{
                height: bucket.count === 0 ? '1px' : `${Math.max(2, (bucket.count / peak) * 100)}%`,
              }}
            />
          </div>
        ))}
      </div>

      {/* Labelled in milliseconds, because the axis is duration. Saying "24h ago" here would be
          the wrong unit entirely. */}
      <div className="chart2__xaxis" style={{ paddingLeft: 0 }}>
        <span>{formatMs(0)}</span>
        <span>{formatMs(slowest / 2)}</span>
        <span>{formatMs(slowest)}</span>
      </div>
    </figure>
  )
}
