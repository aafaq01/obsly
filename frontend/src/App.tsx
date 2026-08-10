import { useEffect, useState } from 'react'
import { Link, Navigate, Route, Routes } from 'react-router-dom'

import { api } from './api'
import { IssueDetailPage } from './pages/IssueDetailPage'
import { Notice } from './components/Notice'
import { Issues } from './pages/Issues'

type Auth = { state: 'loading' } | { state: 'in'; username: string } | { state: 'out' }

export function App() {
  const [auth, setAuth] = useState<Auth>({ state: 'loading' })

  useEffect(() => {
    api
      .me()
      .then(({ username }) => setAuth({ state: 'in', username }))
      .catch(() => setAuth({ state: 'out' }))
  }, [])

  return (
    <div className="app">
      <header className="topbar">
        <Link to="/" className="topbar__brand">
          Obsly
        </Link>
        <div className="topbar__spacer" />
        {auth.state === 'in' && <span className="topbar__user">{auth.username}</span>}
      </header>

      <main className="content">
        {auth.state === 'loading' && <Notice>Checking session…</Notice>}

        {auth.state === 'out' && (
          <Notice>
            <strong>Sign in to continue</strong>
            The UI reads through the same session as the admin. Authentication of its own arrives in
            a later change — until then, <a href="/admin/login/?next=/">sign in here</a> and come
            back.
          </Notice>
        )}

        {auth.state === 'in' && (
          <Routes>
            <Route path="/" element={<Issues />} />
            <Route path="/projects/:projectId/issues" element={<Issues />} />
            <Route path="/issues/:issueId" element={<IssueDetailPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        )}
      </main>
    </div>
  )
}
