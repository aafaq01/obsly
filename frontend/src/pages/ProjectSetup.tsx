import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { api, type ProjectDetail } from '../api'
import { Notice, Skeleton } from '../components/Notice'
import { handle } from '../errors'

type Tier = 'backend' | 'frontend'

const TIERS: { key: Tier; label: string; blurb: string }[] = [
  {
    key: 'backend',
    label: 'Python backend',
    blurb: 'FastAPI, Starlette or any ASGI app. Errors, traces, spans and logs.',
  },
  {
    key: 'frontend',
    label: 'Browser',
    blurb: 'Any page. Errors, Core Web Vitals, and the requests it makes.',
  },
]

/**
 * How to point something at this project.
 *
 * Both tiers, on one page, deliberately. A page load and the backend request it triggers have
 * to be in the same project or the trace cannot join them — which is the whole point of the
 * product — so this is not a choice between them. It is two things to install.
 *
 * The DSN is written into every snippet, so nothing here has to be edited before it is pasted.
 */
export function ProjectSetup() {
  const { projectId } = useParams()
  const id = Number(projectId)

  const [project, setProject] = useState<ProjectDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tier, setTier] = useState<Tier>('backend')
  const [copied, setCopied] = useState<string | null>(null)

  const waiting = (project?.platforms ?? []).length === 0

  useEffect(() => {
    let cancelled = false
    const load = () =>
      api
        .project(id)
        .then((next) => {
          if (!cancelled) setProject(next)
        })
        .catch(handle(setError))

    void load()

    // Only while nothing has arrived. The poll exists to catch the first event so the page
    // turns itself green rather than asking somebody to guess when to refresh — once something
    // has reported there is nothing left to wait for, and a page that keeps polling forever is
    // a page burning requests on a tab nobody is looking at.
    const timer = waiting ? setInterval(() => void load(), 5000) : undefined
    return () => {
      cancelled = true
      if (timer) clearInterval(timer)
    }
  }, [id, waiting])

  if (error) return <Notice>{error}</Notice>
  if (!project) return <Skeleton rows={4} />

  // Array.isArray, not `?? []`: if the response is itself an array then `keys` resolves to
  // Array.prototype.keys — a function, which a nullish check happily passes through. A page
  // that white-screens over an unexpected shape is worse than one that renders the rest and
  // says the key is missing.
  const keys = Array.isArray(project.keys) ? project.keys : []
  const dsn = keys.find((key) => key.is_active)?.dsn ?? ''
  const reporting = Array.isArray(project.platforms) ? project.platforms : []

  const copy = (text: string, what: string) => {
    void navigator.clipboard?.writeText(text)
    setCopied(what)
    setTimeout(() => setCopied(null), 1500)
  }

  return (
    <>
      <div className="page-head">
        <h1>Set up {project.name}</h1>
      </div>

      <p className="page-head__sub">
        One project takes every tier of one application. A page load and the backend request it
        triggers belong in the same project, because that is what lets a single trace hold both — so
        this is not a choice between them.
      </p>

      <div className="section">
        <h2 className="section__title">Your DSN</h2>
        <p className="chart2__caption">
          Safe to ship in a browser bundle. It grants write access to this project and no read
          access to anything.
        </p>
        <div className="card card--tight dsn-row">
          <code className="dsn-row__value">{dsn || 'This project has no active key.'}</code>
          {dsn && (
            <button onClick={() => copy(dsn, 'dsn')}>{copied === 'dsn' ? 'Copied' : 'Copy'}</button>
          )}
        </div>
      </div>

      <div className="section">
        <div className="section__head">
          <h2 className="section__title">Install</h2>
          <div className="seg" role="group" aria-label="Tier">
            {TIERS.map((option) => (
              <button
                key={option.key}
                className={tier === option.key ? 'seg__option seg__option--on' : 'seg__option'}
                aria-pressed={tier === option.key}
                onClick={() => setTier(option.key)}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
        <p className="chart2__caption">{TIERS.find((option) => option.key === tier)?.blurb}</p>

        <div className="card card--tight">
          <pre className="raw">{snippet(tier, dsn)}</pre>
        </div>
        <button className="setup__copy" onClick={() => copy(snippet(tier, dsn), tier)}>
          {copied === tier ? 'Copied' : 'Copy snippet'}
        </button>
      </div>

      <div className="section">
        <h2 className="section__title">What has reported</h2>
        {reporting.length === 0 ? (
          /* Not a spinner. Nothing arriving is a state that can last for hours while somebody
             deploys, and it should say what it is waiting for. */
          <div className="card card--tight">
            <p className="logs__empty">
              Nothing yet. This page checks every few seconds — send one error, or just load a page
              with the browser SDK on it, and the tier will appear here.
            </p>
          </div>
        ) : (
          <div className="card card--tight">
            <p className="setup__seen">
              {reporting.map((name) => (
                <span className="tag tag--sent" key={name}>
                  {name}
                </span>
              ))}
              <span>
                {' '}
                reporting. <Link to={`/projects/${id}/issues`}>Open the issue stream →</Link>
              </span>
            </p>
          </div>
        )}
      </div>
    </>
  )
}

function snippet(tier: Tier, dsn: string): string {
  const placeholder = dsn || 'https://<key>@localhost:8081/<project>'

  if (tier === 'frontend') {
    return `npm install obsly-browser

import { init } from 'obsly-browser'

init({
  dsn: '${placeholder}',
  release: 'web@1.0.0',
  environment: 'production',
})

// Same-origin requests now carry a trace header, so the backend
// request this page makes joins the same trace as the page load.`
  }

  return `pip install obsly

import obsly
from obsly.integrations.fastapi import ObslyMiddleware

obsly.init(
    dsn="${placeholder}",
    release="api@1.0.0",
    environment="production",
    traces_sample_rate=1.0,
    enable_logs=True,
)

app.add_middleware(ObslyMiddleware)`
}
