import { useEffect, useState } from 'react'
import { NavLink, Outlet, useParams } from 'react-router-dom'

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
  const [project, setProject] = useState<Project | null>(null)

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
    return () => {
      cancelled = true
    }
  }, [id])

  return (
    <>
      <div className="subnav">
        <span className="subnav__project">{project?.name ?? `Project ${projectId}`}</span>
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
