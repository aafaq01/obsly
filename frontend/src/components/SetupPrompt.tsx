import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { api } from '../api'

type Tier = 'backend' | 'frontend'

const TIER = {
  backend: {
    missing: 'Nothing from the backend yet',
    action: 'Set up the Python SDK',
    /** What the other half already reporting would mean, once this one arrives. */
    joins: 'a page load and the request it makes would join into one trace',
    other: 'javascript',
  },
  frontend: {
    missing: 'Nothing from the browser yet',
    action: 'Set up the browser SDK',
    joins: 'a page load and the request it makes would join into one trace',
    other: 'python',
  },
} as const

/**
 * An empty state that is also the next step.
 *
 * A layer page with no data is not an error and not really empty — it is a tier nobody has
 * instrumented yet, and the useful thing to show is how to instrument it. Saying "no page loads
 * reported" and stopping leaves the reader to work out that a second SDK exists.
 *
 * It reads what is already reporting, because the sentence that lands is the one about what is
 * missing relative to what is there: a project whose backend is already sending has a specific
 * reason to add the browser, and it is the reason this product exists.
 */
export function SetupPrompt({ tier, children }: { tier: Tier; children?: React.ReactNode }) {
  const { projectId } = useParams()
  const id = Number(projectId)
  const [reporting, setReporting] = useState<string[]>([])

  useEffect(() => {
    let cancelled = false
    api
      .project(id)
      .then((project) => {
        if (!cancelled) setReporting(Array.isArray(project.platforms) ? project.platforms : [])
      })
      .catch(() => setReporting([]))
    return () => {
      cancelled = true
    }
  }, [id])

  const copy = TIER[tier]
  const otherHalfIsIn = reporting.some((name) => name.startsWith(copy.other))

  return (
    <div className="setup-prompt">
      <h3 className="setup-prompt__title">{copy.missing}</h3>
      <p className="setup-prompt__note">
        {otherHalfIsIn
          ? `The other half of this application is already reporting, so once this one is in, ${copy.joins}.`
          : children}
      </p>
      <Link className="button button--primary" to={`/projects/${id}/setup?tier=${tier}`}>
        {copy.action}
      </Link>
    </div>
  )
}
