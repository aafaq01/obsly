import { useEffect, useState } from 'react'
import { Outlet } from 'react-router-dom'

import { api, type Organization, type Project } from '../api'
import { PageFilters } from './PageFilters'
import { Sidebar } from './Sidebar'

/**
 * The frame every signed-in page renders inside.
 *
 * Navigation on the left and filters along the top, both persistent. The alternative — a
 * horizontal tab bar — ran out of room at ten destinations and had no way to group them, so
 * "Spans" and "Settings" sat at the same level and read as equally important.
 */
export function AppShell({ username, onSignOut }: { username: string; onSignOut: () => void }) {
  const [projects, setProjects] = useState<Project[]>([])
  const [organization, setOrganization] = useState<Organization | null>(null)

  useEffect(() => {
    api
      .projects()
      .then(setProjects)
      .catch(() => setProjects([]))
    api
      .organizations()
      .then((orgs) => setOrganization(orgs[0] ?? null))
      .catch(() => setOrganization(null))
  }, [])

  return (
    <div className="shell">
      <Sidebar organization={organization} username={username} onSignOut={onSignOut} />

      <div className="shell__main">
        <PageFilters projects={projects} />
        <main className="shell__content">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
