/** Durations, rendered so the number never lies about its own precision. */
export function formatMs(value: number): string {
  // "0ms" reads as "not measured" rather than "fast".
  if (value > 0 && value < 1) return '<1ms'
  if (value >= 1000) return `${(value / 1000).toFixed(2)}s`
  return `${Math.round(value)}ms`
}

/** Bucket width in words, so an axis can say what one point covers. */
export function bucketLabel(seconds: number): string {
  if (seconds < 60) return seconds === 1 ? 'second' : `${seconds}s`
  if (seconds < 3600) return seconds === 60 ? 'minute' : `${seconds / 60}m`
  if (seconds < 86400) return seconds === 3600 ? 'hour' : `${seconds / 3600}h`
  return seconds === 86400 ? 'day' : `${seconds / 86400}d`
}

/**
 * A span of time, compact and unit-aware — so a 5-minute window is not labelled "0h ago".
 *
 * 86400 reads as "24h" rather than "1d": the window is named "Last 24 hours" in the picker,
 * and an axis that disagrees with the control above it looks like a bug.
 */
export function humanSpan(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`
  if (seconds <= 86400) return `${Math.round(seconds / 3600)}h`
  return `${Math.round(seconds / 86400)}d`
}

export function agoLabel(seconds: number): string {
  if (seconds === 0) return 'now'
  return `${humanSpan(seconds)} ago`
}
