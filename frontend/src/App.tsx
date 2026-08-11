import { useEffect, useState } from 'react'
import { Link, Navigate, Route, Routes } from 'react-router-dom'

import { api } from './api'
import { Notice } from './components/Notice'
import { ProjectLayout } from './components/ProjectLayout'
import { Alerts } from './pages/Alerts'
import { Dashboard } from './pages/Dashboard'
import { EndpointDetail } from './pages/EndpointDetail'
import { IssueDetailPage } from './pages/IssueDetailPage'
import { Issues } from './pages/Issues'
import { Login } from './pages/Login'
import { Logs } from './pages/Logs'
import { Performance } from './pages/Performance'
import { ProjectSettings } from './pages/ProjectSettings'
import { Projects } from './pages/Projects'
import { Queries } from './pages/Queries'
import { SpanDetail } from './pages/SpanDetail'
import { TraceDetail } from './pages/TraceDetail'
import { Traces } from './pages/Traces'

type Auth = { state: 'loading' } | { state: 'in'; username: string } | { state: 'out' }

export function App() {
  const [auth, setAuth] = useState<Auth>({ state: 'loading' })

  useEffect(() => {
    api
      .session()
      .then((session) =>
        setAuth(
          session.authenticated && session.username
            ? { state: 'in', username: session.username }
            : { state: 'out' },
        ),
      )
      .catch(() => setAuth({ state: 'out' }))
  }, [])

  return (
    <div className="app">
      <header className="topbar">
        <Link to="/projects" className="topbar__brand">
          Obsly
        </Link>
        {auth.state === 'in' && (
          <nav className="topbar__nav">
            <Link to="/projects">Projects</Link>
          </nav>
        )}
        <div className="topbar__spacer" />
        {auth.state === 'in' && (
          <>
            <span className="topbar__user">{auth.username}</span>
            <button
              className="button"
              onClick={() => void api.logout().then(() => setAuth({ state: 'out' }))}
            >
              Sign out
            </button>
          </>
        )}
      </header>

      <main className="content">
        {auth.state === 'loading' && <Notice>Checking session…</Notice>}

        {auth.state === 'out' && (
          <Login onSignedIn={(username) => setAuth({ state: 'in', username })} />
        )}

        {auth.state === 'in' && (
          <Routes>
            {/* The project list, not a project. Landing on whichever project sorts first is
                arbitrary, and it reads as a bug the moment you have more than one. */}
            <Route path="/" element={<Navigate to="/projects" replace />} />
            <Route path="/projects" element={<Projects />} />

            {/* A layout route, so the tab bar sits inside a matched route and can read
                :projectId. Every project page renders under it and inherits the tabs. */}
            <Route path="/projects/:projectId" element={<ProjectLayout />}>
              <Route index element={<Navigate to="dashboard" replace />} />
              <Route path="dashboard" element={<Dashboard />} />
              <Route path="issues" element={<Issues />} />
              <Route path="performance" element={<Performance />} />
              <Route path="traces" element={<Traces />} />
              <Route path="spans" element={<Queries />} />
              <Route path="span" element={<SpanDetail />} />
              <Route path="endpoint" element={<EndpointDetail />} />
              <Route path="logs" element={<Logs />} />
              <Route path="alerts" element={<Alerts />} />
              <Route path="settings" element={<ProjectSettings />} />

              {/* Detail pages live under the layout too, so opening an issue does not drop you
                  out of the project you were in and strand you with no way back except the
                  browser button. */}
              <Route path="issues/:issueId" element={<IssueDetailPage />} />
              <Route path="traces/:traceId" element={<TraceDetail />} />
            </Route>

            {/* The old flat links, kept so a pasted or bookmarked URL still resolves. */}
            <Route path="/issues/:issueId" element={<IssueDetailPage />} />
            <Route path="/traces/:traceId" element={<TraceDetail />} />
            <Route path="*" element={<Navigate to="/projects" replace />} />
          </Routes>
        )}
      </main>
    </div>
  )
}
