import { Select } from './Select'
import { PERIODS } from '../periods'

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
    <Select value={value} onChange={(event) => onChange(event.target.value)} aria-label="Period">
      {PERIODS.map((period) => (
        <option key={period.value} value={period.value}>
          {period.label}
        </option>
      ))}
    </Select>
  )
}
