/**
 * The one list of time windows, and the words for them.
 *
 * Lived inside PeriodPicker until a caption needed to name the window it was describing.
 * Exporting non-components from a component file breaks fast refresh, and a second copy of
 * the list is how a caption comes to disagree with the control directly above it.
 */
export const PERIODS = [
  { value: '1m', label: 'Last 1 minute' },
  { value: '5m', label: 'Last 5 minutes' },
  { value: '15m', label: 'Last 15 minutes' },
  { value: '30m', label: 'Last 30 minutes' },
  { value: '1h', label: 'Last hour' },
  { value: '3h', label: 'Last 3 hours' },
  { value: '6h', label: 'Last 6 hours' },
  { value: '12h', label: 'Last 12 hours' },
  { value: '24h', label: 'Last 24 hours' },
  { value: '3d', label: 'Last 3 days' },
  { value: '7d', label: 'Last 7 days' },
  { value: '30d', label: 'Last 30 days' },
]

/** The picker's own words, minus the "Last " so a caption can read as a sentence. */
export function periodLabel(value: string): string {
  return (PERIODS.find((period) => period.value === value)?.label ?? value).replace(/^Last /, '')
}
