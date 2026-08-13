import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'

import { api, type ProjectDetail } from '../api'
import { Notice, Skeleton } from '../components/Notice'
import { handle } from '../errors'

type Tier = 'backend' | 'frontend'

const TIERS: { key: Tier; label: string; blurb: string; file: string }[] = [
  {
    key: 'backend',
    label: 'Python backend',
    blurb: 'FastAPI, Starlette, or any ASGI app. Errors, traces, spans and logs.',
    file: 'main.py',
  },
  {
    key: 'frontend',
    label: 'Browser',
    blurb: 'Any page. Errors, Core Web Vitals, and the requests it makes.',
    file: 'main.ts',
  },
]

/**
 * How to point something at this project.
 *
 * Both tiers, on one page, deliberately. A page load and the backend request it triggers have
 * to be in the same project or the trace cannot join them — which is the whole point of the
 * product — so this is not a choice between them. It is two things to install.
 *
 * Numbered, because the sequence carries information the reader needs rather than decorating
 * one: the snippet cannot be pasted before the DSN exists, and nothing can report before the
 * snippet runs. The last step is the one that answers "did it work", so it is the one that
 * changes on its own.
 */
export function ProjectSetup() {
  const { projectId } = useParams()
  const id = Number(projectId)

  const [project, setProject] = useState<ProjectDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [params, setParams] = useSearchParams()
  const tier: Tier = params.get('tier') === 'frontend' ? 'frontend' : 'backend'
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
  const active = TIERS.find((option) => option.key === tier)

  const copy = (text: string, what: string) => {
    void navigator.clipboard?.writeText(text)
    setCopied(what)
    setTimeout(() => setCopied(null), 1600)
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

      <ol className="steps">
        <li className="step">
          <div className="step__body">
            <h2 className="step__title">Copy your DSN</h2>
            <p className="step__note">
              Safe to ship in a browser bundle. It grants write access to this project and no read
              access to anything.
            </p>

            {dsn ? (
              <div className="keyline">
                <code className="keyline__value">{dsn}</code>
                <button onClick={() => copy(dsn, 'dsn')} aria-label="Copy the DSN">
                  {copied === 'dsn' ? 'Copied' : 'Copy'}
                </button>
              </div>
            ) : (
              <p className="step__note step__note--warn">
                This project has no active key, so nothing can report to it yet. Issue one in{' '}
                <Link to={`/projects/${id}/settings`}>Settings</Link>.
              </p>
            )}
          </div>
        </li>

        <li className="step">
          <div className="step__body">
            <div className="step__head">
              <h2 className="step__title">Install the SDK</h2>
              <div className="seg" role="group" aria-label="Tier">
                {TIERS.map((option) => (
                  <button
                    key={option.key}
                    className={tier === option.key ? 'seg__option seg__option--on' : 'seg__option'}
                    aria-pressed={tier === option.key}
                    onClick={() => setParams({ tier: option.key }, { replace: true })}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </div>
            <p className="step__note">{active?.blurb}</p>

            {/* The action sits on the block it acts on. Floating below the card it read as
                belonging to whatever came next. */}
            <figure className="code">
              <figcaption className="code__bar">
                <span className="code__file mono">{active?.file}</span>
                <button className="code__copy" onClick={() => copy(snippet(tier, dsn), tier)}>
                  {copied === tier ? 'Copied' : 'Copy'}
                </button>
              </figcaption>
              <pre className="code__body">{snippet(tier, dsn)}</pre>
            </figure>
          </div>
        </li>

        <li className={reporting.length > 0 ? 'step step--done' : 'step'}>
          <div className="step__body">
            <h2 className="step__title">Wait for the first event</h2>

            {reporting.length === 0 ? (
              /* Not a spinner. Nothing arriving is a state that can last hours while somebody
                 deploys, and it should name what it is waiting for rather than imply the page
                 itself is still loading. */
              <p className="step__note">
                <span className="pulse" aria-hidden="true" />
                Listening. Send one error, or just load a page with the browser SDK on it — this
                updates on its own.
              </p>
            ) : (
              <p className="step__note">
                <span className="tag tag--sent">reporting</span>{' '}
                {reporting.map((name) => (
                  <code className="mono" key={name}>
                    {name}
                  </code>
                ))}{' '}
                — <Link to={`/projects/${id}/issues`}>open the issue stream</Link>.
              </p>
            )}
          </div>
        </li>
      </ol>
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
