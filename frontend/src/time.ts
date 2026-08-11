const UNITS: [limit: number, seconds: number, suffix: string][] = [
  [60, 1, 's'],
  [3600, 60, 'm'],
  [86400, 3600, 'h'],
  [2592000, 86400, 'd'],
  [Infinity, 2592000, 'mo'],
]

/** Compact age, e.g. "4m", "3h", "12d" — the issue stream shows dozens of these per screen
 *  and a full timestamp per row would crowd out the title that actually identifies the bug. */
export function relativeTime(iso: string): string {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)

  for (const [limit, divisor, suffix] of UNITS) {
    if (seconds < limit) {
      return `${Math.floor(seconds / divisor)}${suffix}`
    }
  }
  return 'now'
}

export function absoluteTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'medium',
  })
}

/**
 * A point on a time axis, at the precision its bucket actually has.
 *
 * Seconds on a 30-day chart are a lie about resolution, and a date on a 5-minute chart is
 * noise repeated on every tick. The bucket width decides which fields are worth printing.
 */
export function clockLabel(date: Date, bucketSeconds: number): string {
  if (bucketSeconds < 60) {
    return date.toLocaleTimeString(undefined, {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })
  }
  if (bucketSeconds < 86400) {
    const time = date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
    // Once a window spans more than a day, the hour alone is ambiguous — 14:00 on which day?
    const sameDay = date.toDateString() === new Date().toDateString()
    return sameDay
      ? time
      : `${date.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })} ${time}`
  }
  return date.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

/** The clock time of one bucket, derived from the series start rather than sent per point. */
export function bucketTime(
  startedAt: string | undefined,
  index: number,
  bucketSeconds: number,
): Date | null {
  if (!startedAt) return null
  const start = new Date(startedAt)
  if (Number.isNaN(start.getTime())) return null
  return new Date(start.getTime() + index * bucketSeconds * 1000)
}
