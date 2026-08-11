import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate, useParams } from 'react-router-dom'

import { api, type Project } from '../api'

const TABS = [
  { to: 'dashboard', label: 'Overview' },
  { to: 'issues', label: 'Issues' },
  { to: 'performance', label: 'Performance' },
  { to: 'traces', label: 'Traces' },
  { to: 'spans', label: 'Spans' },
  { to: 'logs', label: 'Logs' },
  { to: 'settings', label: 'Settings' },
]

/**
 * Everything scoped to one project: the tab bar, and the page under it.
 *
 * A layout route rather than a component rendered beside <Routes>. useParams only resolves
 * inside a matched route — rendered outside one it returns an empty object, so the tab bar
 * silently rendered nothing and every page it linked to was unreachable.
 */
export function ProjectLayout() {
  const { projectId } = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const [project, setProject] = useState<Project | null>(null)
  const [projects, setProjects] = useState<Project[]>([])

  const id = Number(projectId)

  useEffect(() => {
    let cancelled = false
    api
      .project(id)
      .then((next) => {
        if (!cancelled) setProject(next)
      })
      // The name is decoration. Failing to load it must not blank the page under it.
      .catch(() => undefined)
    // The switcher list is decoration on this page too — failing to load it must not blank
    // the page under it.
    api
      .projects()
      .then((all) => {
        if (!cancelled) setProjects(all)
      })
      .catch(() => undefined)

    return () => {
      cancelled = true
    }
  }, [id])

  return (
    <>
      <div className="subnav">
        {/* Switching project keeps you on the same tab. Being thrown back to a list every
            time you compare two services is the kind of friction that makes people stop
            comparing. */}
        {projects.length > 1 ? (
          <select
            className="switcher"
            aria-label="Project"
            value={id}
            onChange={(event) => {
              // Only the tab segment travels. Carrying the whole sub-path would take
              // /projects/1/issues/9 to /projects/2/issues/9 — project 1's issue rendered
              // under project 2's header — and would drop the query string that identifies a
              // span, landing on a detail page with nothing to detail.
              //
              // From the router rather than window.location: under a MemoryRouter the two
              // disagree, which makes the component untestable and wrong inside any nested
              // routing context.
              const tab = location.pathname.split('/')[3] ?? 'dashboard'
              void navigate(`/projects/${event.target.value}/${tab}`)
            }}
          >
            {projects.map((option) => (
              <option key={option.id} value={option.id}>
                {option.name}
              </option>
            ))}
          </select>
        ) : (
          <span className="subnav__project">{project?.name ?? `Project ${projectId}`}</span>
        )}
        <nav className="subnav__tabs">
          {TABS.map((tab) => (
            <NavLink key={tab.to} to={`/projects/${projectId}/${tab.to}`}>
              {tab.label}
            </NavLink>
          ))}
        </nav>
      </div>

      <Outlet />
    </>
  )
}
