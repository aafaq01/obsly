import { useEffect, useState } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'

import { api } from './api'
import { Notice } from './components/Notice'
import { AppShell } from './components/AppShell'
import { Alerts } from './pages/Alerts'
import { Dashboard } from './pages/Dashboard'
import { Database } from './pages/Database'
import { EndpointDetail } from './pages/EndpointDetail'
import { IssueDetailPage } from './pages/IssueDetailPage'
import { Issues } from './pages/Issues'
import { Login } from './pages/Login'
import { Logs } from './pages/Logs'
import { Performance } from './pages/Performance'
import { ProjectSettings } from './pages/ProjectSettings'
import { Projects } from './pages/Projects'
import { SpanDetail } from './pages/SpanDetail'
import { TraceDetail } from './pages/TraceDetail'
import { Releases } from './pages/Releases'
import { Traces } from './pages/Traces'
import { Vitals } from './pages/Vitals'
import { Cache } from './pages/Cache'

type Auth =
  | { state: 'loading' }
  | { state: 'in'; username: string }
  // `firstRun` is the case with no users at all: the form opens on register, because a
  // sign-in form is a door with no key cut for it.
  | { state: 'out'; signupOpen: boolean; firstRun: boolean }

export function App() {
  const [auth, setAuth] = useState<Auth>({ state: 'loading' })

  useEffect(() => {
    api
      .session()
      .then((session) =>
        setAuth(
          session.authenticated && session.username
            ? { state: 'in', username: session.username }
            : {
                state: 'out',
                signupOpen: session.signup_open ?? false,
                // Open registration on an install that already has users is a team choosing
                // to leave the door open; open on an install with none is a first run.
                firstRun: session.signup_open === true && session.username === null,
              },
        ),
      )
      .catch(() => setAuth({ state: 'out', signupOpen: false, firstRun: false }))
  }, [])

  return (
    <div className="app">
      <main className="app__body">
        {auth.state === 'loading' && <Notice>Checking session…</Notice>}

        {auth.state === 'out' && (
          <Login
            signupOpen={auth.signupOpen}
            firstRun={auth.firstRun}
            onSignedIn={(username) => setAuth({ state: 'in', username })}
          />
        )}

        {auth.state === 'in' && (
          <Routes>
            <Route
              element={
                <AppShell
                  username={auth.username}
                  onSignOut={() =>
                    void api
                      .logout()
                      .then(() => setAuth({ state: 'out', signupOpen: false, firstRun: false }))
                  }
                />
              }
            >
              {/* The project list, not a project. Landing on whichever project sorts first is
                  arbitrary, and it reads as a bug the moment you have more than one. */}
              <Route path="/" element={<Navigate to="/projects" replace />} />
              <Route path="/projects" element={<Projects />} />

              <Route path="/projects/:projectId">
                {/* Issues first, the way Sentry lands: the question you open an observability
                    tool to ask is "what is broken", not "what are the averages". */}
                <Route index element={<Navigate to="issues" replace />} />
                <Route path="issues" element={<Issues />} />
                <Route path="dashboard" element={<Dashboard />} />

                <Route path="traces" element={<Traces />} />
                <Route path="logs" element={<Logs />} />

                {/* One page per tier of the stack. */}
                <Route path="insights/frontend" element={<Vitals />} />
                <Route path="insights/backend" element={<Performance />} />
                <Route path="insights/database" element={<Database />} />
                <Route path="insights/cache" element={<Cache />} />

                <Route path="releases" element={<Releases />} />
                <Route path="alerts" element={<Alerts />} />
                <Route path="settings" element={<ProjectSettings />} />

                {/* Detail pages live under the shell too, so opening an issue does not drop you
                    out of the project you were in. */}
                <Route path="span" element={<SpanDetail />} />
                <Route path="endpoint" element={<EndpointDetail />} />
                <Route path="issues/:issueId" element={<IssueDetailPage />} />
                <Route path="traces/:traceId" element={<TraceDetail />} />

                {/* The tab names before Insights existed. A bookmark or a link in an old
                    incident channel must still land where it meant to. */}
                <Route path="vitals" element={<Navigate to="../insights/frontend" replace />} />
                <Route path="performance" element={<Navigate to="../insights/backend" replace />} />
                <Route path="spans" element={<Navigate to="../insights/database" replace />} />
              </Route>

              {/* The old flat links, kept so a pasted or bookmarked URL still resolves. */}
              <Route path="/issues/:issueId" element={<IssueDetailPage />} />
              <Route path="/traces/:traceId" element={<TraceDetail />} />
              <Route path="*" element={<Navigate to="/projects" replace />} />
            </Route>
          </Routes>
        )}
      </main>
    </div>
  )
}
