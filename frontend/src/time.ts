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
