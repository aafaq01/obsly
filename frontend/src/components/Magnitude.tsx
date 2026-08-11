import type { CSSProperties } from 'react'

interface Props {
  value: number
  /** The largest value in this column, so the bar is relative to its own column and not to
   *  some absolute scale that means nothing. */
  max: number
  children: React.ReactNode
  className?: string
}

/**
 * A measured value with a bar of its own size behind it.
 *
 * A column of latencies answers "which row is the problem" only if you read every number. With
 * a proportional fill the column reads as a chart, and the outlier is visible before anybody
 * parses a digit.
 *
 * The number stays in front and stays exact — this adds a second channel, it does not replace
 * the first one.
 */
export function Magnitude({ value, max, children, className = '' }: Props) {
  // Guard the divide: an all-zero column would otherwise fill every row completely and imply
  // every row is the worst one.
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0

  return (
    <td className={`num mag ${className}`} style={{ '--mag': `${pct}%` } as CSSProperties}>
      {children}
    </td>
  )
}
