import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'

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

  const live = project.keys.find((key) => key.is_active)

  return (
    <>
      <Breadcrumbs trail={[{ label: 'Projects', to: '/projects' }, { label: project.name }]} />

      <h1 className="page-title">{project.name}</h1>
      <p className="page-subtitle">
        {project.organization} · {project.platform}
      </p>

      <div className="section">
        <h2 className="section__title">Getting started</h2>
        <div className="card" style={{ padding: 16 }}>
          {live ? (
            <>
              <p style={{ marginTop: 0 }}>
                Install the SDK and point it at this DSN. Everything below is copy-paste.
              </p>
              <pre className="raw">{snippet(live.dsn, project.platform)}</pre>
            </>
          ) : (
            <Notice>
              <strong>No active key</strong>
              This project cannot receive events until a key is issued below.
            </Notice>
          )}
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

function snippet(dsn: string, platform: string): string {
  if (platform === 'javascript' || platform === 'node') {
    return `// not yet available — the browser SDK lands in a later change.\n// DSN: ${dsn}`
  }
  return `pip install obsly-sdk

import obsly
from obsly.integrations.fastapi import ObslyMiddleware

obsly.init(
    dsn="${dsn}",
    release="myapp@1.0.0",
    environment="production",
)

app.add_middleware(ObslyMiddleware)`
}
