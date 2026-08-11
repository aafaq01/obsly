import { NavLink, useParams } from 'react-router-dom'

/**
 * Tabs scoped to the current project.
 *
 * Percentiles and traces both existed before this and were effectively unreachable — a feature
 * nobody can navigate to has not really shipped.
 */
export function ProjectNav() {
  const { projectId } = useParams()
  if (!projectId) return null

  return (
    <div className="subnav">
      <nav className="subnav__tabs">
        <NavLink to={`/projects/${projectId}/dashboard`}>Overview</NavLink>
        <NavLink to={`/projects/${projectId}/issues`}>Issues</NavLink>
        <NavLink to={`/projects/${projectId}/performance`}>Performance</NavLink>
        <NavLink to={`/projects/${projectId}/traces`}>Traces</NavLink>
        <NavLink to={`/projects/${projectId}/spans`}>Spans</NavLink>
        <NavLink to={`/projects/${projectId}/logs`}>Logs</NavLink>
        <NavLink to={`/projects/${projectId}/settings`}>Settings</NavLink>
      </nav>
    </div>
  )
}
