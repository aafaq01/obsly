import { Link } from 'react-router-dom'

interface Row {
  label: string
  sublabel?: string
  value: number
  href?: string
}

interface Props {
  rows: Row[]
  /** Rendered after each value, e.g. "ms" or " calls". */
  format: (value: number) => string
  /** What the length of a bar means, said once rather than implied. */
  caption: string
  limit?: number
}

/**
 * A ranked horizontal bar chart.
 *
 * Horizontal because the labels are SQL statements and route patterns — text that needs a line
 * of its own. A vertical bar chart would rotate them, and a rotated statement is unreadable at
 * exactly the moment somebody is scanning for one.
 *
 * One series, one colour. This ranks by a single measure; colouring each bar differently would
 * double-encode length as hue and spend the only free channel on information already shown.
 */
export function RankChart({ rows, format, caption, limit = 8 }: Props) {
  const shown = rows.slice(0, limit)
  const max = shown.reduce((peak, row) => Math.max(peak, row.value), 0)

  if (shown.length === 0) {
    return <p className="logs__empty">Nothing to rank yet.</p>
  }

  return (
    <figure className="rank">
      <figcaption className="rank__caption">{caption}</figcaption>
      {shown.map((row, index) => {
        const width = max > 0 ? Math.max(0.5, (row.value / max) * 100) : 0
        // Labels collide — two ops can share a description, and every empty one renders as
        // "(no description)". A duplicate key makes React reuse the wrong node on re-sort.
        const key = `${row.sublabel ?? ''}:${row.label}:${index}`
        const body = (
          <>
            <span className="rank__label">
              <span className="rank__name">{row.label}</span>
              {row.sublabel && <span className="rank__sub">{row.sublabel}</span>}
            </span>
            <span className="rank__track">
              <span className="rank__bar" style={{ width: `${width}%` }} />
            </span>
            <span className="rank__value">{format(row.value)}</span>
          </>
        )

        // Link, not <a href>: a raw anchor full-page-reloads the SPA and ignores any
        // router basename. The href-only assertion in the test passed either way, which is
        // why this needed reading rather than running.
        return row.href ? (
          <Link className="rank__row" key={key} to={row.href}>
            {body}
          </Link>
        ) : (
          <div className="rank__row" key={key}>
            {body}
          </div>
        )
      })}
      {rows.length > limit && (
        // Saying what was cut, because a top-8 that looks like the whole list is a chart that
        // lies by omission.
        <p className="rank__more">
          {rows.length - limit} more below the top {limit}
        </p>
      )}
    </figure>
  )
}
