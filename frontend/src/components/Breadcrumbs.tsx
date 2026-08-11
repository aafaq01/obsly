import { Link } from 'react-router-dom'

export interface Crumb {
  label: string
  /** Omitted on the last crumb: it is where you already are. */
  to?: string
}

/**
 * The trail back out of a detail page.
 *
 * Detail pages were reachable and not leaveable — a trace had no back link at all, so the only
 * way out was the browser button, and a page you can only leave by browser button is a page
 * that feels like a dead end even though it technically is not.
 *
 * Rendered as a nav landmark so it is skippable and announced, rather than a row of anonymous
 * links.
 */
export function Breadcrumbs({ trail }: { trail: Crumb[] }) {
  return (
    <nav className="crumbs" aria-label="Breadcrumb">
      <ol>
        {trail.map((crumb, index) => {
          const last = index === trail.length - 1
          return (
            <li key={`${crumb.label}-${index}`}>
              {crumb.to && !last ? (
                <Link to={crumb.to}>{crumb.label}</Link>
              ) : (
                <span aria-current={last ? 'page' : undefined}>{crumb.label}</span>
              )}
            </li>
          )
        })}
      </ol>
    </nav>
  )
}
