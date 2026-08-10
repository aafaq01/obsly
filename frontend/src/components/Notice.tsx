/** Empty, loading and error states all share one shape, so they read as the same kind of
 *  message rather than three different failures. */
export function Notice({ children }: { children: React.ReactNode }) {
  return <div className="notice">{children}</div>
}
