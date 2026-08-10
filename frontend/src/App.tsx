import { useEffect, useState } from 'react'
import { Link, Navigate, Route, Routes } from 'react-router-dom'

import { api } from './api'
import { IssueDetailPage } from './pages/IssueDetailPage'
import { Notice } from './components/Notice'
import { Issues } from './pages/Issues'
import { Login } from './pages/Login'
import { ProjectSettings } from './pages/ProjectSettings'
import { Projects } from './pages/Projects'

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
        <Link to="/" className="topbar__brand">
          Obsly
        </Link>
        {auth.state === 'in' && (
          <nav className="topbar__nav">
            <Link to="/">Issues</Link>
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
            <Route path="/" element={<Issues />} />
            <Route path="/projects/:projectId/issues" element={<Issues />} />
            <Route path="/issues/:issueId" element={<IssueDetailPage />} />
            <Route path="/projects" element={<Projects />} />
            <Route path="/projects/:projectId/settings" element={<ProjectSettings />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        )}
      </main>
    </div>
  )
}
