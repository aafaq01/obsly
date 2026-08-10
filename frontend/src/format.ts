/** Durations, rendered so the number never lies about its own precision. */
export function formatMs(value: number): string {
  // "0ms" reads as "not measured" rather than "fast".
  if (value > 0 && value < 1) return '<1ms'
  if (value >= 1000) return `${(value / 1000).toFixed(2)}s`
  return `${Math.round(value)}ms`
}
