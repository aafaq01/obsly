const PERIODS = [
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

/**
 * One control, one list, everywhere.
 *
 * Every page had its own hardcoded set, which is how two pages come to disagree about what
 * "last 24 hours" offers.
 */
export function PeriodPicker({
  value,
  onChange,
}: {
  value: string
  onChange: (period: string) => void
}) {
  return (
    <select value={value} onChange={(event) => onChange(event.target.value)} aria-label="Period">
      {PERIODS.map((period) => (
        <option key={period.value} value={period.value}>
          {period.label}
        </option>
      ))}
    </select>
  )
}
