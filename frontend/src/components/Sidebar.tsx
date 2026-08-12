import { NavLink, useParams } from 'react-router-dom'

import type { Organization } from '../api'

/**
 * The persistent navigation.
 *
 * Grouped, because a flat list of fourteen destinations is a list nobody reads. The groups are
 * the question each page answers, not the table it reads from:
 *
 * - Explore is "show me the raw records" — traces, logs, spans.
 * - Insights is "how is this layer doing" — one page per tier of the stack.
 *
 * Everything here is project-scoped except the project list itself, so the links carry the
 * project id and the bar disables itself until one is chosen.
 */
const GROUPS: { title?: string; items: { to: string; label: string; icon: string }[] }[] = [
  {
    items: [
      { to: 'issues', label: 'Issues', icon: '◉' },
      { to: 'dashboard', label: 'Dashboard', icon: '▦' },
    ],
  },
  {
    title: 'Explore',
    items: [
      { to: 'traces', label: 'Traces', icon: '≣' },
      { to: 'logs', label: 'Logs', icon: '☰' },
    ],
  },
  {
    // One page per tier of the stack. A slow checkout is a browser problem, a handler problem
    // or a query problem, and those are three different people's afternoons — splitting them
    // is what turns "the site is slow" into a specific thing to fix.
    title: 'Insights',
    items: [
      { to: 'insights/frontend', label: 'Frontend', icon: '▢' },
      { to: 'insights/backend', label: 'Backend', icon: '▤' },
      { to: 'insights/database', label: 'Database', icon: '▥' },
      { to: 'insights/cache', label: 'Cache', icon: '▨' },
    ],
  },
  {
    items: [
      { to: 'releases', label: 'Releases', icon: '⌥' },
      { to: 'alerts', label: 'Alerts', icon: '⚑' },
    ],
  },
]

export function Sidebar({
  organization,
  username,
  onSignOut,
}: {
  organization: Organization | null
  username: string
  onSignOut: () => void
}) {
  const { projectId } = useParams()

  return (
    <nav className="sidebar" aria-label="Primary">
      <div className="sidebar__head">
        <NavLink to="/" className="sidebar__brand">
          Obsly
        </NavLink>
        {/* The organisation is the ownership boundary every project hangs off. It is a label
            here rather than a switcher: with one organisation a switcher is a control with a
            single option, which is furniture pretending to be a choice. */}
        <span className="sidebar__org" title="Organization">
          {organization?.name ?? '—'}
        </span>
      </div>

      <div className="sidebar__scroll">
        <NavLink to="/projects" className="sidebar__link">
          <span className="sidebar__icon" aria-hidden="true">
            ⬡
          </span>
          Projects
        </NavLink>

        {projectId ? (
          GROUPS.map((group, index) => (
            <div className="sidebar__group" key={group.title ?? index}>
              {group.title && <div className="sidebar__title">{group.title}</div>}
              {group.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={`/projects/${projectId}/${item.to}`}
                  className="sidebar__link"
                >
                  <span className="sidebar__icon" aria-hidden="true">
                    {item.icon}
                  </span>
                  {item.label}
                </NavLink>
              ))}
            </div>
          ))
        ) : (
          /* Not a disabled menu. Every destination below needs a project, and a row of greyed
             links invites clicks that do nothing. */
          <p className="sidebar__hint">Choose a project to see its issues, traces and insights.</p>
        )}
      </div>

      <div className="sidebar__foot">
        {projectId && (
          <NavLink to={`/projects/${projectId}/settings`} className="sidebar__link">
            <span className="sidebar__icon" aria-hidden="true">
              ⚙
            </span>
            Settings
          </NavLink>
        )}
        <div className="sidebar__user">
          <span className="sidebar__avatar" aria-hidden="true">
            {username.slice(0, 1).toUpperCase()}
          </span>
          <span className="sidebar__username">{username}</span>
          <button className="sidebar__signout" onClick={onSignOut}>
            Sign out
          </button>
        </div>
      </div>
    </nav>
  )
}
