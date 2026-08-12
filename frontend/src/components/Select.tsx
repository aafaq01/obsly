import type { SelectHTMLAttributes } from 'react'

/**
 * A native select that looks like it belongs here.
 *
 * The element itself is untouched — same keyboard behaviour, same OS popup on mobile, same
 * screen-reader semantics. Only the closed control is restyled, because the platform draws its
 * arrow in the platform's colour and has no way to be told the page is dark.
 *
 * The wrapper exists solely to hang that chevron on. A custom listbox would let the open panel
 * be styled too, and would also mean rebuilding focus, type-ahead and touch behaviour that the
 * native element already gets right.
 */
export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <span className="select-wrap">
      <select {...props} />
    </span>
  )
}
