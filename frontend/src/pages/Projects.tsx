import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { api, type Organization, type Project } from '../api'
import { Notice } from '../components/Notice'
import { handle } from '../errors'

const PLATFORMS = ['python', 'javascript', 'node', 'other']

export function Projects() {
  const [projects, setProjects] = useState<Project[] | null>(null)
  const [organizations, setOrganizations] = useState<Organization[]>([])
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    api.projects().then(setProjects).catch(handle(setError))
    api.organizations().then(setOrganizations).catch(handle(setError))
  }, [])

  if (error) return <Notice>{error}</Notice>

  return (
    <>
      <h1 className="page-title">Projects</h1>
      <p className="page-subtitle">One project per deployable. Each has its own ingest keys.</p>

      <div className="filters">
        <button className="button button--primary" onClick={() => setCreating(!creating)}>
          {creating ? 'Cancel' : 'New project'}
        </button>
      </div>

      {creating && (
        <NewProject
          organizations={organizations}
          onCreated={(project) => {
            setProjects((current) => [...(current ?? []), project])
            setOrganizations((current) => current)
            setCreating(false)
          }}
          onOrganizationCreated={(org) => setOrganizations((current) => [...current, org])}
        />
      )}

      {projects === null ? (
        <Notice>Loading…</Notice>
      ) : projects.length === 0 ? (
        <Notice>
          <strong>No projects yet</strong>
          Create one to get a DSN.
        </Notice>
      ) : (
        <div className="card">
          {projects.map((project) => (
            <div
              className="issue-row"
              key={project.id}
              style={{ gridTemplateColumns: '1fr 110px 110px' }}
            >
              <div>
                <p className="issue-row__title">
                  <Link to={`/projects/${project.id}/issues`}>{project.name}</Link>
                </p>
                <div className="issue-row__meta">
                  <span>{project.organization}</span>
                  <span className="issue-row__culprit">{project.platform}</span>
                </div>
              </div>
              <div className="issue-row__num">
                {project.unresolved_count}
                <span>unresolved</span>
              </div>
              <div className="issue-row__num">
                <Link to={`/projects/${project.id}/settings`} style={{ color: 'var(--series-1)' }}>
                  Settings
                </Link>
                <span>DSN &amp; keys</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  )
}

interface NewProjectProps {
  organizations: Organization[]
  onCreated: (project: Project) => void
  onOrganizationCreated: (organization: Organization) => void
}

function NewProject({ organizations, onCreated, onOrganizationCreated }: NewProjectProps) {
  const [name, setName] = useState('')
  const [platform, setPlatform] = useState('python')
  const [organizationId, setOrganizationId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const selected = organizationId ?? organizations[0]?.id ?? null

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      // A first-run install has no organization, and making someone create one before they can
      // create a project is a dead end on an empty screen.
      let orgId = selected
      if (orgId === null) {
        const created = await api.createOrganization('Default', 'default')
        onOrganizationCreated(created)
        orgId = created.id
      }

      onCreated(
        await api.createProject({
          name,
          slug: slugify(name),
          platform,
          organization_id: orgId,
        }),
      )
      setName('')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not create the project.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form
      className="card"
      style={{ padding: 16, marginBottom: 16 }}
      onSubmit={(e) => void submit(e)}
    >
      <div className="filters" style={{ marginBottom: 0 }}>
        <input
          placeholder="Project name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          aria-label="Project name"
          required
        />
        <select
          value={platform}
          onChange={(event) => setPlatform(event.target.value)}
          aria-label="Platform"
        >
          {PLATFORMS.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
        {organizations.length > 0 && (
          <select
            value={selected ?? ''}
            onChange={(event) => setOrganizationId(Number(event.target.value))}
            aria-label="Organization"
          >
            {organizations.map((org) => (
              <option key={org.id} value={org.id}>
                {org.name}
              </option>
            ))}
          </select>
        )}
        <button className="button button--primary" type="submit" disabled={busy}>
          {busy ? 'Creating…' : 'Create'}
        </button>
      </div>
      {name && (
        <p className="page-subtitle" style={{ margin: '10px 0 0' }}>
          slug: {slugify(name)}
        </p>
      )}
      {error && (
        <p className="login__error" role="alert">
          {error}
        </p>
      )}
    </form>
  )
}

function slugify(value: string): string {
  return value
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 100)
}
