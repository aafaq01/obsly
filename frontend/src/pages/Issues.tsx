import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { api, type Issue, type Project } from '../api'
import { Notice } from '../components/Notice'
import { handle } from '../errors'
import { EventChart } from '../components/EventChart'
import { relativeTime } from '../time'

export function Issues() {
  const { projectId } = useParams()
  const [projects, setProjects] = useState<Project[]>([])
  const [issues, setIssues] = useState<Issue[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('unresolved')
  const [sort, setSort] = useState('last_seen')

  useEffect(() => {
    api.projects().then(setProjects).catch(handle(setError))
  }, [])

  const active = projectId ? Number(projectId) : projects[0]?.id

  useEffect(() => {
    if (active === undefined) return

    const params = new URLSearchParams({ status, sort })
    if (query) params.set('q', query)

    // Debounced so typing does not fire a request per keystroke.
    const timer = setTimeout(() => {
      setIssues(null)
      api.issues(active, params).then(setIssues).catch(handle(setError))
    }, 200)

    return () => clearTimeout(timer)
  }, [active, query, status, sort])

  if (error) return <Notice>{error}</Notice>

  const project = projects.find((p) => p.id === active)

  return (
    <>
      <h1 className="page-title">{project ? project.name : 'Issues'}</h1>
      <p className="page-subtitle">
        {project ? `${project.organization} · ${project.platform}` : 'Loading projects…'}
        {project && (
          <>
            {' · '}
            <Link to={`/projects/${project.id}/performance`} style={{ color: 'var(--series-1)' }}>
              Performance
            </Link>
            {' · '}
            <Link to={`/projects/${project.id}/settings`} style={{ color: 'var(--series-1)' }}>
              Settings
            </Link>
          </>
        )}
      </p>

      <div className="filters">
        <input
          type="search"
          placeholder="Search issues by title or culprit"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          aria-label="Search issues"
        />
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
          <option value="unresolved">Unresolved</option>
          <option value="resolved">Resolved</option>
          <option value="ignored">Ignored</option>
          <option value="all">All</option>
        </select>
        <select value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort by">
          <option value="last_seen">Last seen</option>
          <option value="first_seen">First seen</option>
          <option value="times_seen">Events</option>
        </select>
        {projects.length > 1 && (
          <select
            value={active ?? ''}
            onChange={(e) => {
              window.location.href = `/projects/${e.target.value}/issues`
            }}
            aria-label="Project"
          >
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} ({p.unresolved_count})
              </option>
            ))}
          </select>
        )}
      </div>

      {issues === null ? (
        <Notice>Loading issues…</Notice>
      ) : issues.length === 0 ? (
        <Notice>
          <strong>No issues yet</strong>
          Point an SDK at this project&rsquo;s DSN and trigger an error. Nothing here means nothing
          has been reported — not that nothing is wrong.
        </Notice>
      ) : (
        <div className="card">
          {issues.map((issue) => (
            <IssueRow key={issue.id} issue={issue} />
          ))}
        </div>
      )}
    </>
  )
}

function IssueRow({ issue }: { issue: Issue }) {
  return (
    <Link to={`/issues/${issue.id}`} className="issue-row">
      <div>
        <p className="issue-row__title">{issue.title}</p>
        <div className="issue-row__meta">
          <span className={`level level--${issue.level}`}>{issue.level}</span>
          {issue.culprit && <span className="issue-row__culprit">{issue.culprit}</span>}
          <span>{relativeTime(issue.last_seen)}</span>
        </div>
      </div>
      <EventChart hourly={issue.hourly} compact />
      <div className="issue-row__num">
        {issue.times_seen.toLocaleString()}
        <span>events</span>
      </div>
      <div className="issue-row__num">
        {relativeTime(issue.first_seen)}
        <span>age</span>
      </div>
    </Link>
  )
}
