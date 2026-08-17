import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { api, type ProjectDetail, type ProjectKey } from '../api'
import { Breadcrumbs } from '../components/Breadcrumbs'
import { Notice } from '../components/Notice'
import { handle } from '../errors'
import { absoluteTime } from '../time'

export function ProjectSettings() {
  const { projectId } = useParams()
  const [project, setProject] = useState<ProjectDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  const id = Number(projectId)

  useEffect(() => {
    api.project(id).then(setProject).catch(handle(setError))
  }, [id])

  if (error) return <Notice>{error}</Notice>
  if (!project) return <Notice>Loading project…</Notice>

  async function issueKey() {
    const created = await api.createKey(id, 'rotation')
    setProject((current) => (current ? { ...current, keys: [created, ...current.keys] } : current))
  }

  async function toggleKey(key: ProjectKey) {
    const updated = await api.setKeyActive(key.id, !key.is_active)
    setProject((current) =>
      current
        ? { ...current, keys: current.keys.map((k) => (k.id === updated.id ? updated : k)) }
        : current,
    )
  }

  async function toggleTraceSharing(next: boolean) {
    const updated = await api.setTraceSharing(id, next)
    setProject((current) =>
      current ? { ...current, trace_sharing: updated.trace_sharing } : current,
    )
  }

  const live = project.keys.find((key) => key.is_active)

  return (
    <>
      <Breadcrumbs trail={[{ label: 'Projects', to: '/projects' }, { label: project.name }]} />

      <h1 className="page-title">{project.name}</h1>
      <p className="page-subtitle">
        {project.organization}
        {project.platforms.length > 0 && ` · ${project.platforms.join(' · ')}`}
      </p>

      <div className="section">
        <h2 className="section__title">Getting started</h2>
        <div className="card card--tight">
          {live ? (
            <div className="settings-cta">
              <p className="settings-cta__note">
                Install snippets for the backend and the browser, with this project&rsquo;s DSN
                already in them.
              </p>
              <Link className="button button--primary" to={`/projects/${project.id}/setup`}>
                Open setup
              </Link>
            </div>
          ) : (
            <Notice>
              <strong>No active key</strong>
              This project cannot receive events until a key is issued below.
            </Notice>
          )}
        </div>
      </div>

      <div className="section">
        <h2 className="section__title">Distributed tracing</h2>
        <div className="card card--tight">
          {/* Off until somebody decides otherwise: joining is a disclosure, and a project that
              appeared in another team's waterfall without anyone choosing it would be a
              decision nobody made. */}
          <label className="toggle">
            <input
              type="checkbox"
              checked={project.trace_sharing}
              onChange={(event) => void toggleTraceSharing(event.target.checked)}
            />
            <span>
              <span className="toggle__title">
                Combine traces with other projects in {project.organization}
              </span>
              <span className="toggle__note">
                One request often crosses several services, and each has its own project. With this
                on, a trace opened here shows every hop from projects that have also turned it on —
                and this project&rsquo;s hops appear in theirs. With it off, this project is never
                pulled into anyone else&rsquo;s waterfall.
              </span>
            </span>
          </label>
        </div>
      </div>

      <div className="section">
        <h2 className="section__title">Ingest keys</h2>
        <div className="card">
          <div style={{ padding: 12, borderBottom: '1px solid var(--border)' }}>
            <button className="button" onClick={() => void issueKey()}>
              Issue a new key
            </button>
            <span style={{ marginLeft: 10, color: 'var(--ink-muted)', fontSize: 12.5 }}>
              Rotation is issue-new, migrate clients, then revoke. Revoking is reversible — a key is
              deactivated, never deleted.
            </span>
          </div>
          {project.keys.map((key) => (
            <div key={key.id} className="keyrow">
              <div>
                <div className="keyrow__label">
                  {key.label}
                  <span
                    className={`status-pill status-pill--${key.is_active ? 'resolved' : 'ignored'}`}
                  >
                    {key.is_active ? 'active' : 'revoked'}
                  </span>
                </div>
                <code className="keyrow__dsn">{key.dsn}</code>
                <div className="keyrow__when">Created {absoluteTime(key.created_at)}</div>
              </div>
              <button className="button" onClick={() => void toggleKey(key)}>
                {key.is_active ? 'Revoke' : 'Restore'}
              </button>
            </div>
          ))}
        </div>
      </div>
    </>
  )
}
