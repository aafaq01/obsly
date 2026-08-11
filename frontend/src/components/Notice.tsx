/** Empty, loading and error states all share one shape, so they read as the same kind of
 *  message rather than three different failures. */
export function Notice({ children }: { children: React.ReactNode }) {
  return <div className="notice">{children}</div>
}

/**
 * A placeholder that holds the shape of what is arriving.
 *
 * Replacing a page with the word "Loading" and then swapping in a table moves everything under
 * the cursor at the moment somebody was about to click. Holding the layout is the difference
 * between a page that loads and a page that lurches.
 */
export function Skeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="skeleton" aria-busy="true" aria-live="polite" aria-label="Loading">
      {Array.from({ length: rows }, (_, index) => (
        <div className="skeleton__row" key={index} />
      ))}
    </div>
  )
}
