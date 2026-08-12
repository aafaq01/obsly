import { useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'

import type { Project } from '../api'
import { PERIODS } from '../periods'

/** Pages identified by a query string rather than a path segment. Switching project to one of
 *  these without its query string lands on a detail page with nothing to detail, so the list
 *  it belongs to is the honest destination. */
const DETAIL_PARENT: Record<string, string> = {
  span: 'insights/database',
  endpoint: 'insights/backend',
}

/**
 * The filter bar: what you are looking at, and over what window.
 *
 * One bar above every page rather than a period picker inside each one. The window is a
 * property of the question you are asking, not of the page you happen to be on, and it must
 * survive moving between pages — noticing a spike on one page and losing the window on the way
 * to another is the most common way an investigation stalls.
 *
 * It writes to the URL, which every page already reads. That also makes the whole view
 * shareable: the link somebody pastes into an incident channel carries the window they were
 * looking at.
 */
export function PageFilters({ projects }: { projects: Project[] }) {
  const { projectId } = useParams()
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const [params, setParams] = useSearchParams()

  const period = params.get('period') ?? '24h'

  const setPeriod = (next: string) => {
    const updated = new URLSearchParams(params)
    updated.set('period', next)
    setParams(updated, { replace: true })
  }

  const switchProject = (next: string) => {
    // Same section, different project. Being thrown back to a list every time you compare two
    // services is the friction that makes people stop comparing.
    //
    // Only the section travels, never a record. Carrying the whole sub-path would take
    // /projects/1/issues/9 to /projects/2/issues/9 — project 1's issue rendered under project
    // 2's header. Detail pages that are identified by a query string rather than a path
    // segment fall back to the list they belong to, so switching never lands on a detail page
    // with nothing to detail.
    //
    // From the router's location, not window.location: under a MemoryRouter the two disagree,
    // which makes this untestable and wrong inside any nested routing context.
    const [, , , first = 'issues', second] = pathname.split('/')
    const section = first === 'insights' && second ? `insights/${second}` : first
    const target = DETAIL_PARENT[section] ?? section

    // The window is a property of the question, not of the project, so it survives the switch.
    const carried = new URLSearchParams()
    const currentPeriod = params.get('period')
    if (currentPeriod) carried.set('period', currentPeriod)

    void navigate({
      pathname: `/projects/${next}/${target}`,
      search: carried.toString() ? `?${carried.toString()}` : '',
    })
  }

  return (
    <div className="pagebar">
      {/* Only once there is something to choose. A select rendered empty and then filled is
          a control that briefly claims the project list is empty. */}
      {projects.length > 0 && (
        <label className="pagebar__field">
          <span className="pagebar__label">Project</span>
          <select
            value={projectId ?? ''}
            onChange={(event) => switchProject(event.target.value)}
            aria-label="Project"
          >
            {!projectId && <option value="">All projects</option>}
            {projects.map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))}
          </select>
        </label>
      )}

      <label className="pagebar__field">
        <span className="pagebar__label">Period</span>
        <select
          value={period}
          onChange={(event) => setPeriod(event.target.value)}
          aria-label="Period"
        >
          {PERIODS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
    </div>
  )
}
