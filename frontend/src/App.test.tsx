import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { App } from './App'
import { EventChart } from './components/EventChart'
import { relativeTime } from './time'

interface Route {
  status?: number
  body: unknown
}

function mockApi(routes: Record<string, Route>) {
  // Longest key first: "/projects/1/issues/" also startsWith "/projects/", and matching the
  // shorter one served the project list as the issue list.
  const keys = Object.keys(routes).sort((a, b) => b.length - a.length)

  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    void init
    const match = keys.find((key) => url.startsWith(`/api/0${key}`))
    if (!match) {
      return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) })
    }
    const route = routes[match]!
    const status = route.status ?? 200
    return Promise.resolve({
      ok: status < 400,
      status,
      json: () => Promise.resolve(route.body),
    })
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const PROJECT = {
  id: 1,
  name: 'Checkout',
  slug: 'checkout',
  platform: 'python',
  organization: 'Acme',
  unresolved_count: 2,
}

const ISSUE = {
  id: 9,
  project: 1,
  title: 'ValueError: cart is empty',
  culprit: 'app.crud in get_cart',
  level: 'error',
  status: 'unresolved',
  times_seen: 128,
  first_seen: new Date(Date.now() - 7200_000).toISOString(),
  last_seen: new Date(Date.now() - 120_000).toISOString(),
  hourly: [0, 0, 3, 7, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 4, 12],
  category: 'error' as const,
  issue_type: '',
  evidence: {},
}

function renderApp() {
  return render(
    <MemoryRouter initialEntries={['/projects/1/issues']}>
      <App />
    </MemoryRouter>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('App authentication', () => {
  it('shows its own sign-in form rather than sending you to the admin', async () => {
    mockApi({ '/me/': { body: { authenticated: false, username: null } } })

    renderApp()

    expect(await screen.findByRole('heading', { name: /Sign in to Obsly/ })).toBeInTheDocument()
    expect(screen.getByLabelText('Username')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /admin/i })).not.toBeInTheDocument()
  })

  it('signs in and lands on the issue stream', async () => {
    mockApi({
      '/me/': { body: { authenticated: false, username: null } },
      '/auth/login/': { body: { authenticated: true, username: 'admin' } },
      '/projects/': { body: [PROJECT] },
      '/projects/1/issues/': { body: [ISSUE] },
    })

    renderApp()

    await userEvent.type(await screen.findByLabelText('Username'), 'admin')
    await userEvent.type(screen.getByLabelText('Password'), 'hunter2')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByText('ValueError: cart is empty')).toBeInTheDocument()
  })

  it('shows the server message when credentials are wrong', async () => {
    mockApi({
      '/me/': { body: { authenticated: false, username: null } },
      '/auth/login/': { status: 401, body: { detail: 'Incorrect username or password.' } },
    })

    renderApp()

    await userEvent.type(await screen.findByLabelText('Username'), 'admin')
    await userEvent.type(screen.getByLabelText('Password'), 'wrong')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Incorrect username or password.')
  })

  it('signs out', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/auth/logout/': { body: { authenticated: false, username: null } },
      '/projects/': { body: [PROJECT] },
      '/projects/1/issues/': { body: [] },
    })

    renderApp()
    await userEvent.click(await screen.findByRole('button', { name: 'Sign out' }))

    expect(await screen.findByRole('heading', { name: /Sign in to Obsly/ })).toBeInTheDocument()
  })

  it('shows the signed-in user', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/': { body: [PROJECT] },
      '/projects/1/issues/': { body: [] },
    })

    renderApp()

    expect(await screen.findByText('admin')).toBeInTheDocument()
  })
})

describe('Issue stream', () => {
  it('renders an issue with its title, culprit and event count', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/': { body: [PROJECT] },
      '/projects/1/issues/': { body: [ISSUE] },
    })

    renderApp()

    expect(await screen.findByText('ValueError: cart is empty')).toBeInTheDocument()
    expect(screen.getByText('app.crud in get_cart')).toBeInTheDocument()
    expect(screen.getByText('128')).toBeInTheDocument()
  })

  it('says nothing was reported rather than implying nothing is wrong', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/': { body: [PROJECT] },
      '/projects/1/issues/': { body: [] },
    })

    renderApp()

    expect(await screen.findByText(/No issues yet/)).toBeInTheDocument()
    expect(screen.getByText(/not that nothing is wrong/)).toBeInTheDocument()
  })

  it('sends the search term to the API', async () => {
    const fetchMock = mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/': { body: [PROJECT] },
      '/projects/1/issues/': { body: [ISSUE] },
    })

    renderApp()
    await screen.findByText('ValueError: cart is empty')

    await userEvent.type(screen.getByLabelText('Search issues'), 'cart')

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((call) => String(call[0]))
      expect(urls.some((url) => url.includes('q=cart'))).toBe(true)
    })
  })

  it('surfaces an expired session distinctly from a generic failure', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/': { status: 401, body: {} },
    })

    renderApp()

    expect(await screen.findByText(/session expired/i)).toBeInTheDocument()
  })
})

describe('EventChart', () => {
  it('describes itself for screen readers', () => {
    render(<EventChart hourly={[1, 2, 3]} />)

    expect(screen.getByRole('img')).toHaveAccessibleName(/6 total/)
  })

  it('shows the total and the peak without needing a hover', () => {
    // A tooltip that is the only route to a number is unreachable by keyboard and gone the
    // moment you look away.
    render(<EventChart hourly={[0, 5, 3]} />)

    expect(screen.getByText(/8/)).toBeInTheDocument()
    expect(screen.getByText(/peak 5/)).toBeInTheDocument()
  })

  it('labels the axis so the window is readable without hovering', () => {
    render(<EventChart hourly={Array<number>(24).fill(1)} />)

    expect(screen.getByText('24h ago')).toBeInTheDocument()
    expect(screen.getByText('now')).toBeInTheDocument()
  })

  it('offers a table view of the values', () => {
    render(<EventChart hourly={[0, 7]} />)

    expect(screen.getByRole('button', { name: /table/i })).toBeInTheDocument()
  })

  it('renders a baseline tick for an empty hour', () => {
    render(<EventChart hourly={[0, 0, 0]} />)

    expect(document.querySelectorAll('.bars__bar--empty')).toHaveLength(3)
  })

  it('labels the axis in clock time when it knows when the series starts', () => {
    // "3h ago" cannot be lined up against a deploy, an alert, or somebody else's screenshot.
    // A wall-clock time can.
    render(
      <EventChart
        hourly={[1, 2, 3]}
        bucketSeconds={3600}
        startedAt="2026-08-12T09:00:00Z"
        unit="requests"
      />,
    )

    const axis = document.querySelector('.chart2__xaxis')!
    // Rendered in the viewer's zone, so assert the shape rather than a fixed hour.
    expect(axis.textContent).toMatch(/\d{1,2}:\d{2}/)
    expect(axis.textContent).not.toMatch(/ago/)
  })

  it('still says how long ago when no start time is available', () => {
    // Charts whose endpoint has not been widened yet must keep working, not lose their axis.
    render(<EventChart hourly={Array<number>(24).fill(1)} />)

    expect(screen.getByText('24h ago')).toBeInTheDocument()
  })

  it('states what it is measuring rather than leaving it to the heading', () => {
    render(<EventChart hourly={[1]} caption="Requests handled per hour across every endpoint." />)

    expect(screen.getByText('Requests handled per hour across every endpoint.')).toBeInTheDocument()
  })
})

describe('relativeTime', () => {
  it.each([
    [30_000, '30s'],
    [300_000, '5m'],
    [7_200_000, '2h'],
    [259_200_000, '3d'],
  ])('formats %ims ago', (ms, expected) => {
    expect(relativeTime(new Date(Date.now() - ms).toISOString())).toBe(expected)
  })

  it('never shows a negative age for a clock-skewed future timestamp', () => {
    expect(relativeTime(new Date(Date.now() + 60_000).toISOString())).toBe('0s')
  })
})

describe('StatusActions', () => {
  const ISSUE_DETAIL = {
    issue: { ...ISSUE, fingerprint: 'abc', fingerprint_components: ['ValueError'] },
    latest_event: null,
    tags: {},
  }

  function renderDetail() {
    return render(
      <MemoryRouter initialEntries={['/issues/9']}>
        <App />
      </MemoryRouter>,
    )
  }

  it('offers only the transitions valid from the current status', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/issues/9/': { body: ISSUE_DETAIL },
    })

    renderDetail()

    expect(await screen.findByRole('button', { name: 'Resolve' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Ignore' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reopen' })).not.toBeInTheDocument()
  })

  it('sends the CSRF token from the cookie on a mutation', async () => {
    document.cookie = 'csrftoken=token-from-cookie'
    const fetchMock = mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/issues/9/status/': { body: { ...ISSUE, status: 'resolved' } },
      '/issues/9/': { body: ISSUE_DETAIL },
    })

    renderDetail()
    await userEvent.click(await screen.findByRole('button', { name: 'Resolve' }))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find((c) => String(c[0]).includes('/status/'))
      expect(call).toBeDefined()
      const init = call?.[1]
      expect(init?.method).toBe('PATCH')
      expect((init?.headers as Record<string, string>)['X-CSRFToken']).toBe('token-from-cookie')
    })
  })

  it('does not claim success when the server rejects the change', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/issues/9/status/': { status: 500, body: {} },
      '/issues/9/': { body: ISSUE_DETAIL },
    })

    renderDetail()
    await userEvent.click(await screen.findByRole('button', { name: 'Resolve' }))

    // Still offering "Resolve" — the UI must not show a state the server never accepted.
    expect(await screen.findByRole('button', { name: 'Resolve' })).toBeInTheDocument()
    expect(screen.getByText(/returned 500/)).toBeInTheDocument()
  })
})

describe('Project settings', () => {
  const DETAIL = {
    ...PROJECT,
    keys: [
      {
        id: 3,
        label: 'default',
        public_key: 'abc123',
        dsn: 'http://abc123@localhost:8081/1',
        is_active: true,
        created_at: new Date().toISOString(),
      },
    ],
  }

  function renderSettings() {
    return render(
      <MemoryRouter initialEntries={['/projects/1/settings']}>
        <App />
      </MemoryRouter>,
    )
  }

  it('shows the DSN so nobody has to open the admin for it', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/': { body: DETAIL },
    })

    renderSettings()

    expect(await screen.findByText('http://abc123@localhost:8081/1')).toBeInTheDocument()
  })

  it('includes a copy-paste install snippet carrying the DSN', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/': { body: DETAIL },
    })

    renderSettings()

    const snippet = await screen.findByText(/ObslyMiddleware/)
    expect(snippet).toHaveTextContent('http://abc123@localhost:8081/1')
  })

  it('warns when no key is active, because the project cannot receive events', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/': {
        body: { ...DETAIL, keys: [{ ...DETAIL.keys[0], is_active: false }] },
      },
    })

    renderSettings()

    expect(await screen.findByText(/No active key/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Restore' })).toBeInTheDocument()
  })

  it('revokes a key', async () => {
    const fetchMock = mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/': { body: DETAIL },
      '/keys/3/': { body: { ...DETAIL.keys[0], is_active: false } },
    })

    renderSettings()
    await userEvent.click(await screen.findByRole('button', { name: 'Revoke' }))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find((c) => String(c[0]).includes('/keys/3/'))
      expect(call?.[1]?.method).toBe('PATCH')
    })
    expect(await screen.findByRole('button', { name: 'Restore' })).toBeInTheDocument()
  })
})

describe('Projects page', () => {
  it('creates an organization first when there are none', async () => {
    // Otherwise a first-run install is a dead end: you must create an org you cannot see a
    // reason for before you can create the project you came for.
    const fetchMock = mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/organizations/': { body: [] },
      '/projects/': { body: [] },
    })

    render(
      <MemoryRouter initialEntries={['/projects']}>
        <App />
      </MemoryRouter>,
    )

    await userEvent.click(await screen.findByRole('button', { name: 'New project' }))
    await userEvent.type(screen.getByLabelText('Project name'), 'My Service')
    await userEvent.click(screen.getByRole('button', { name: 'Create' }))

    await waitFor(() => {
      const orgPost = fetchMock.mock.calls.find(
        (c) => String(c[0]).includes('/organizations/') && c[1]?.method === 'POST',
      )
      expect(orgPost).toBeDefined()
    })
  })

  it('derives a slug from the project name', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/organizations/': { body: [{ id: 1, name: 'Acme', slug: 'acme' }] },
      '/projects/': { body: [] },
    })

    render(
      <MemoryRouter initialEntries={['/projects']}>
        <App />
      </MemoryRouter>,
    )

    await userEvent.click(await screen.findByRole('button', { name: 'New project' }))
    await userEvent.type(screen.getByLabelText('Project name'), 'Checkout API!!')

    expect(await screen.findByText('slug: checkout-api')).toBeInTheDocument()
  })
})

describe('Performance page', () => {
  const PERF = {
    period: '24h',
    endpoints: [
      {
        name: '/checkout',
        op: 'http.server',
        count: 1440,
        throughput_per_minute: 1.0,
        failure_rate: 0.05,
        total_ms: 432000,
        p50: 20,
        p75: 24,
        p95: 8000,
        p99: 9100,
      },
      {
        name: '/health',
        op: 'http.server',
        count: 100,
        throughput_per_minute: 0.07,
        failure_rate: 0,
        total_ms: 60,
        p50: 0.6,
        p75: 0.7,
        p95: 0.9,
        p99: 1,
      },
    ],
    summary: {
      transactions: 1540,
      throughput_per_minute: 1.07,
      failure_rate: 0.047,
      series: Array<number>(24).fill(64),
      bucket_seconds: 3600,
    },
  }

  function renderPerf() {
    return render(
      <MemoryRouter initialEntries={['/projects/1/performance']}>
        <App />
      </MemoryRouter>,
    )
  }

  it('shows every percentile per endpoint', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/performance/': { body: PERF },
    })

    renderPerf()

    // Scoped to the table: the rank chart above it lists the same endpoints, and an unscoped
    // query cannot tell which one it found.
    const table = within(await screen.findByRole('table'))
    expect(table.getByText('/checkout')).toBeInTheDocument()
    expect(table.getByText('20ms')).toBeInTheDocument()
    expect(table.getByText('8.00s')).toBeInTheDocument()
    expect(table.getByText('9.10s')).toBeInTheDocument()
  })

  it('ranks by time spent, so a rarely-called slow endpoint does not top the list', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/performance/': { body: PERF },
    })

    renderPerf()

    const table = within(await screen.findByRole('table'))
    const names = table.getAllByText(/^\/(checkout|health)$/).map((n) => n.textContent)
    expect(names[0]).toBe('/checkout')
  })

  it('renders sub-millisecond latency as <1ms, not 0ms', async () => {
    // "0ms" reads as "not measured" rather than "fast".
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/performance/': { body: PERF },
    })

    renderPerf()

    const table = within(await screen.findByRole('table'))
    expect(table.getAllByText('<1ms')).not.toHaveLength(0)
  })

  it('explains that tracing is off rather than showing an empty table', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/performance/': {
        body: {
          ...PERF,
          endpoints: [],
          summary: { transactions: 0, throughput_per_minute: 0, failure_rate: 0, hourly: [] },
        },
      },
    })

    renderPerf()

    expect(await screen.findByText(/No transactions yet/)).toBeInTheDocument()
    expect(screen.getByText(/traces_sample_rate/)).toBeInTheDocument()
  })

  it('refetches when the period changes', async () => {
    const fetchMock = mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/performance/': { body: PERF },
    })

    renderPerf()
    await screen.findByRole('table')
    await userEvent.selectOptions(screen.getByLabelText('Period'), '7d')

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((c) => String(c[0]))
      expect(urls.some((url) => url.includes('period=7d'))).toBe(true)
    })
  })
})

describe('Issue detail layout', () => {
  const DETAIL = {
    issue: { ...ISSUE, fingerprint: 'abc', fingerprint_components: ['ValueError', 'app.crud:get'] },
    latest_event: {
      id: '6ba7b810-9dad-11d1-80b4-00c04fd430c8',
      timestamp: new Date().toISOString(),
      received_at: new Date().toISOString(),
      level: 'error',
      platform: 'python',
      message: '',
      exception_type: 'ValueError',
      exception_value: 'cart is empty',
      culprit: 'app.crud in get_cart',
      environment: 'production',
      release: 'checkout@1.4.2',
      server_name: 'web-1',
      tags: {},
      exception: [
        {
          type: 'ValueError',
          value: 'cart is empty',
          frames: [
            {
              filename: 'a.py',
              module: 'app.crud',
              function: 'get_cart',
              lineno: 42,
              in_app: true,
            },
            { filename: 'b.py', module: 'lib.orm', function: 'execute', lineno: 9, in_app: false },
          ],
        },
      ],
      payload: { huge: 'x'.repeat(500) },
    },
    tags: {},
  }

  function renderIssue() {
    return render(
      <MemoryRouter initialEntries={['/issues/9']}>
        <App />
      </MemoryRouter>,
    )
  }

  it('keeps the raw payload collapsed so it cannot push the stack trace off screen', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/issues/9/': { body: DETAIL },
    })

    renderIssue()

    const payload = await screen.findByText('Raw payload')
    expect(payload.closest('details')).not.toHaveAttribute('open')
  })

  it('shows reference values inline rather than as hero tiles', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/issues/9/': { body: DETAIL },
    })

    renderIssue()

    expect(await screen.findByText('Events')).toBeInTheDocument()
    expect(screen.getByText('checkout@1.4.2')).toBeInTheDocument()
    expect(screen.getByText('production')).toBeInTheDocument()
  })

  it('hides system frames behind a count by default', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/issues/9/': { body: DETAIL },
    })

    renderIssue()

    // Queried by function name: the module sits in a text node beside a <strong>, so a
    // whole-string matcher never matches.
    expect(await screen.findByText('get_cart')).toBeInTheDocument()
    expect(screen.queryByText('execute')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /1 more system frame/ })).toBeInTheDocument()
  })
})

describe('Correlation', () => {
  const EVENT = {
    id: '6ba7b810-9dad-11d1-80b4-00c04fd430c8',
    timestamp: new Date().toISOString(),
    received_at: new Date().toISOString(),
    level: 'error',
    platform: 'python',
    message: '',
    exception_type: 'ConnectionError',
    exception_value: '502',
    culprit: 'main in checkout',
    environment: 'production',
    release: 'demo@1.3.0',
    server_name: 'web-1',
    trace_id: 'a'.repeat(32),
    span_id: 'b'.repeat(16),
    tags: {},
    exception: [{ type: 'ConnectionError', value: '502', frames: [] }],
    payload: {},
  }

  it('links an issue to the request it happened inside', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/issues/9/': {
        body: {
          issue: { ...ISSUE, fingerprint: 'f', fingerprint_components: [] },
          latest_event: EVENT,
          tags: {},
          trace: {
            id: 'trace-uuid',
            name: '/checkout/{cart_id}',
            duration_ms: 131,
            status: 'internal_error',
          },
        },
      },
    })

    render(
      <MemoryRouter initialEntries={['/issues/9']}>
        <App />
      </MemoryRouter>,
    )

    expect(await screen.findByText('/checkout/{cart_id}')).toBeInTheDocument()
    expect(screen.getByText(/View trace/)).toBeInTheDocument()
  })

  it('shows no trace banner when the error happened outside one', async () => {
    // A dead link is worse than admitting there is nothing to link to.
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/issues/9/': {
        body: {
          issue: { ...ISSUE, fingerprint: 'f', fingerprint_components: [] },
          latest_event: { ...EVENT, trace_id: '' },
          tags: {},
          trace: null,
        },
      },
    })

    render(
      <MemoryRouter initialEntries={['/issues/9']}>
        <App />
      </MemoryRouter>,
    )

    // The title, not the breadcrumb — both now carry the issue title, which is the point of
    // the breadcrumb.
    await screen.findByRole('heading', { name: 'ValueError: cart is empty' })
    expect(screen.queryByText(/View trace/)).not.toBeInTheDocument()
  })

  it('lists the errors that happened inside a trace', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/traces/trace-uuid/': {
        body: {
          id: 'trace-uuid',
          trace_id: 'a'.repeat(32),
          span_id: 'b'.repeat(16),
          name: '/checkout/{cart_id}',
          op: 'http.server',
          status: 'internal_error',
          start_timestamp: new Date().toISOString(),
          timestamp: new Date().toISOString(),
          duration_ms: 131,
          environment: 'production',
          release: 'demo@1.3.0',
          span_count: 1,
          spans: [
            {
              span_id: 'c'.repeat(16),
              parent_span_id: 'b'.repeat(16),
              op: 'http.client',
              description: 'POST payments.example.com/charge',
              status: 'internal_error',
              start_timestamp: new Date().toISOString(),
              timestamp: new Date().toISOString(),
              duration_ms: 111.7,
              data: {},
            },
          ],
          errors: [
            {
              id: 'e1',
              issue_id: 9,
              title: 'ConnectionError: 502',
              level: 'error',
              timestamp: new Date().toISOString(),
              span_id: 'c'.repeat(16),
            },
          ],
          logs: [
            {
              id: 'l1',
              timestamp: new Date().toISOString(),
              level: 'info',
              body: 'charging card for cart c-1',
              logger: 'demo',
              trace_id: 'a'.repeat(32),
              span_id: 'c'.repeat(16),
              environment: 'production',
              release: 'demo@1.3.0',
              attributes: {},
            },
          ],
        },
      },
    })

    render(
      <MemoryRouter initialEntries={['/traces/trace-uuid']}>
        <App />
      </MemoryRouter>,
    )

    expect(await screen.findByText('Errors in this request')).toBeInTheDocument()
    expect(screen.getByText('ConnectionError: 502')).toBeInTheDocument()
    expect(screen.getByText('POST payments.example.com/charge')).toBeInTheDocument()
    // The third signal for the same request: what the application was saying while it ran.
    expect(screen.getByText('Logs from this request')).toBeInTheDocument()
    expect(screen.getByText('charging card for cart c-1')).toBeInTheDocument()
  })
})

describe('Logs', () => {
  const RECORDS = [
    {
      id: 'l1',
      timestamp: new Date().toISOString(),
      level: 'info',
      body: 'checkout complete for cart c-1',
      logger: 'demo',
      trace_id: 'a'.repeat(32),
      span_id: 'b'.repeat(16),
      environment: 'production',
      release: 'demo@1.3.0',
      attributes: {},
    },
    {
      id: 'l2',
      timestamp: new Date().toISOString(),
      level: 'warning',
      body: 'analytics rollup on the request path',
      logger: 'demo',
      trace_id: '',
      span_id: '',
      environment: 'production',
      release: 'demo@1.3.0',
      attributes: {},
    },
  ]

  function renderLogs(entry = '/projects/1/logs') {
    return render(
      <MemoryRouter initialEntries={[entry]}>
        <App />
      </MemoryRouter>,
    )
  }

  it('lists records with level and logger', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/logs/': { body: RECORDS },
    })

    renderLogs()

    expect(await screen.findByText('checkout complete for cart c-1')).toBeInTheDocument()
    expect(screen.getByText('analytics rollup on the request path')).toBeInTheDocument()
  })

  it('offers a toggle per level rather than a single-select', async () => {
    // "warning and worse" and "only warnings" are different questions, and a single-select
    // control can only ask one of them.
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/logs/': { body: RECORDS },
    })

    renderLogs()

    for (const level of ['trace', 'debug', 'info', 'warning', 'error', 'fatal']) {
      expect(await screen.findByRole('button', { name: level })).toBeInTheDocument()
    }
    expect(screen.getByRole('button', { name: 'All' })).toBeInTheDocument()
  })

  it('sends only the levels that are toggled on', async () => {
    const fetchMock = mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/logs/': { body: RECORDS },
    })

    renderLogs()
    await userEvent.click(await screen.findByRole('button', { name: 'warning' }))

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((c) => String(c[0]))
      expect(urls.some((url) => url.includes('levels=warning'))).toBe(true)
    })
  })

  it('marks a toggled level with aria-pressed, not colour alone', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/logs/': { body: RECORDS },
    })

    renderLogs('/projects/1/logs?levels=error')

    expect(await screen.findByRole('button', { name: 'error' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(screen.getByRole('button', { name: 'info' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('filters to a single request when a trace is given', async () => {
    const fetchMock = mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/logs/': { body: [RECORDS[0]] },
    })

    renderLogs(`/projects/1/logs?trace_id=${'a'.repeat(32)}`)

    expect(await screen.findByText(/Filtered to one request/)).toBeInTheDocument()
    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((c) => String(c[0]))
      expect(urls.some((url) => url.includes('trace_id=aaaa'))).toBe(true)
    })
  })

  it('explains that logs are off rather than showing an empty stream', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/logs/': { body: [] },
    })

    renderLogs()

    expect(await screen.findByText(/No logs yet/)).toBeInTheDocument()
    expect(screen.getByText(/enable_logs=True/)).toBeInTheDocument()
  })
})

describe('Navigation', () => {
  it('lands on the project list, not on whichever project sorts first', async () => {
    // Landing on an arbitrary project reads as a bug the moment you have more than one.
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/': { body: [PROJECT, { ...PROJECT, id: 2, name: 'Billing' }] },
      '/organizations/': { body: [] },
    })

    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    )

    expect(await screen.findByRole('heading', { name: 'Projects' })).toBeInTheDocument()
  })

  it('shows every project tab once inside a project', async () => {
    // These rendered nothing before: the tab bar sat outside <Routes>, so useParams returned
    // an empty object and every page it linked to was unreachable.
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/': { body: { ...PROJECT, keys: [] } },
      '/projects/1/issues/': { body: [] },
    })

    render(
      <MemoryRouter initialEntries={['/projects/1/issues']}>
        <App />
      </MemoryRouter>,
    )

    for (const tab of [
      'Overview',
      'Issues',
      'Performance',
      'Traces',
      'Spans',
      'Logs',
      'Settings',
    ]) {
      expect(await screen.findByRole('link', { name: tab })).toBeInTheDocument()
    }
  })

  it('names the project in the tab bar', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/': { body: { ...PROJECT, keys: [] } },
      '/projects/1/issues/': { body: [] },
    })

    render(
      <MemoryRouter initialEntries={['/projects/1/issues']}>
        <App />
      </MemoryRouter>,
    )

    expect(await screen.findByText('Checkout')).toBeInTheDocument()
  })

  it('a bare project url opens the overview', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/': { body: { ...PROJECT, keys: [] } },
      '/projects/1/dashboard/': {
        body: {
          period: '24h',
          buckets: 24,
          headline: {
            transactions: 5,
            throughput_per_minute: 0.1,
            failure_rate: 0,
            p95_ms: 12,
            errors: 0,
            unresolved_issues: 0,
            logs: 0,
          },
          series: {
            throughput: [5],
            failures: [0],
            errors: [0],
            logs: [0],
            p95: [12],
          },
          top_issues: [],
          slowest_endpoints: [],
        },
      },
    })

    render(
      <MemoryRouter initialEntries={['/projects/1']}>
        <App />
      </MemoryRouter>,
    )

    expect(await screen.findByRole('heading', { name: 'Overview' })).toBeInTheDocument()
  })
})

describe('Performance issues', () => {
  const PERF_ISSUE = {
    ...ISSUE,
    id: 11,
    title: 'N+1 Queries: SELECT total FROM orders WHERE id = %s',
    culprit: '/report',
    level: 'warning',
    category: 'performance' as const,
    issue_type: 'n_plus_one_queries',
    evidence: {
      description: 'SELECT total FROM orders WHERE id = %s',
      op: 'db.query',
      repeat_count: 25,
      total_ms: 250,
      wasted_ms: 240,
      transaction: '/report',
      trace_id: 'a'.repeat(32),
    },
  }

  it('marks a performance issue in the stream', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/': { body: { ...PROJECT, keys: [] } },
      '/projects/1/issues/': { body: [PERF_ISSUE] },
    })

    render(
      <MemoryRouter initialEntries={['/projects/1/issues']}>
        <App />
      </MemoryRouter>,
    )

    expect(await screen.findByText('n plus one queries')).toBeInTheDocument()
  })

  it('shows what the detector found instead of an empty stack trace', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/issues/11/': {
        body: {
          issue: { ...PERF_ISSUE, fingerprint: 'f', fingerprint_components: [] },
          latest_event: null,
          tags: {},
          trace: null,
        },
      },
    })

    render(
      <MemoryRouter initialEntries={['/issues/11']}>
        <App />
      </MemoryRouter>,
    )

    expect(await screen.findByText('What the detector found')).toBeInTheDocument()
    expect(screen.getByText('SELECT total FROM orders WHERE id = %s')).toBeInTheDocument()
    expect(screen.getByText('25')).toBeInTheDocument()
    // The number that says whether fixing this is worth an afternoon.
    expect(screen.getByText('240ms')).toBeInTheDocument()
  })
})

describe('Instrument-panel UI', () => {
  it('shows a skeleton that holds the layout rather than a loading sentence', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/': { body: { ...PROJECT, keys: [] } },
      '/projects/1/issues/': { body: new Promise(() => {}) },
    })

    render(
      <MemoryRouter initialEntries={['/projects/1/issues']}>
        <App />
      </MemoryRouter>,
    )

    expect(await screen.findByLabelText('Loading')).toBeInTheDocument()
    expect(document.querySelectorAll('.skeleton__row').length).toBeGreaterThan(0)
  })
})

describe('Span detail', () => {
  const DETAIL = {
    op: 'db.query',
    description: 'SELECT total FROM orders WHERE id = %s',
    period: '24h',
    summary: {
      count: 150,
      transactions: 6,
      per_transaction: 25,
      total_ms: 364.7,
      p50: 2.4,
      p95: 2.8,
      p99: 3.1,
      slowest: 4,
    },
    distribution: Array.from({ length: 20 }, (_, i) => ({
      from_ms: i * 0.2,
      to_ms: (i + 1) * 0.2,
      count: i === 3 ? 120 : i === 19 ? 1 : 0,
    })),
    callers: [{ transaction: '/report', count: 150, total_ms: 364.7 }],
    samples: [
      {
        duration_ms: 4,
        trace_id: 'a'.repeat(32),
        transaction_id: 'txn-uuid',
        transaction: '/report',
        transaction_ms: 62,
        timestamp: new Date().toISOString(),
      },
    ],
  }

  function renderDetail() {
    return render(
      <MemoryRouter
        initialEntries={['/projects/1/span?op=db.query&description=SELECT%20total%20FROM%20orders']}
      >
        <App />
      </MemoryRouter>,
    )
  }

  it('names the endpoints that make the span expensive', async () => {
    // The aggregate says a query is expensive; this says who makes it expensive.
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/': { body: { ...PROJECT, keys: [] } },
      '/projects/1/span/': { body: DETAIL },
    })

    renderDetail()

    expect(await screen.findByText('Which endpoints call it')).toBeInTheDocument()
    expect(screen.getAllByText('/report').length).toBeGreaterThan(0)
  })

  it('offers a trace to open, linked into the waterfall', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/': { body: { ...PROJECT, keys: [] } },
      '/projects/1/span/': { body: DETAIL },
    })

    renderDetail()

    await screen.findByText('Traces to open · slowest first')
    const links = screen.getAllByRole('link').map((a) => a.getAttribute('href'))
    expect(links).toContain('/projects/1/traces/txn-uuid')
  })

  it('draws the distribution so the shape is visible, not just two percentiles', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/': { body: { ...PROJECT, keys: [] } },
      '/projects/1/span/': { body: DETAIL },
    })

    renderDetail()

    await screen.findByText('How long these calls take')
    expect(document.querySelectorAll('.dist__bar')).toHaveLength(20)
  })

  it('explains an empty window rather than showing a broken page', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/': { body: { ...PROJECT, keys: [] } },
      '/projects/1/span/': { status: 404, body: { detail: 'No spans matched in this period.' } },
    })

    renderDetail()

    expect(await screen.findByText(/Could not load/)).toBeInTheDocument()
  })
})

describe('Navigation depth', () => {
  it('gives a trace a way back out', async () => {
    // It had none: the only exit was the browser button, and a page you can only leave that
    // way feels like a dead end even though it technically is not.
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/': { body: { ...PROJECT, keys: [] } },
      '/traces/t1/': {
        body: {
          id: 't1',
          trace_id: 'a'.repeat(32),
          span_id: 'b'.repeat(16),
          name: '/checkout',
          op: 'http.server',
          status: 'ok',
          start_timestamp: new Date().toISOString(),
          timestamp: new Date().toISOString(),
          duration_ms: 12,
          environment: 'production',
          release: 'r@1',
          span_count: 0,
          spans: [],
          errors: [],
          logs: [],
        },
      },
    })

    render(
      <MemoryRouter initialEntries={['/projects/1/traces/t1']}>
        <App />
      </MemoryRouter>,
    )

    const crumb = await screen.findByRole('navigation', { name: 'Breadcrumb' })
    expect(within(crumb).getByRole('link', { name: 'Traces' })).toHaveAttribute(
      'href',
      '/projects/1/traces',
    )
  })

  it('does not link the page you are already on', async () => {
    // A link to where you already are is a dead control, and one dead control teaches people
    // the whole trail is decorative.
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/': { body: { ...PROJECT, keys: [] } },
      '/issues/9/': {
        body: {
          issue: { ...ISSUE, fingerprint: 'f', fingerprint_components: [] },
          latest_event: null,
          tags: {},
          trace: null,
        },
      },
    })

    render(
      <MemoryRouter initialEntries={['/projects/1/issues/9']}>
        <App />
      </MemoryRouter>,
    )

    const crumb = await screen.findByRole('navigation', { name: 'Breadcrumb' })
    const current = within(crumb).getByText('ValueError: cart is empty')
    expect(current).toHaveAttribute('aria-current', 'page')
    expect(current.tagName).not.toBe('A')
  })

  it('keeps you on the same tab when you switch project', async () => {
    // Being thrown back to a list every time you compare two services is the friction that
    // makes people stop comparing.
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/': { body: { ...PROJECT, keys: [] } },
      '/projects/': { body: [PROJECT, { ...PROJECT, id: 2, name: 'Billing' }] },
      '/projects/1/logs/': { body: [] },
      '/projects/2/logs/': { body: [] },
      '/projects/2/': { body: { ...PROJECT, id: 2, name: 'Billing', keys: [] } },
    })

    render(
      <MemoryRouter initialEntries={['/projects/1/logs']}>
        <App />
      </MemoryRouter>,
    )

    await userEvent.selectOptions(await screen.findByLabelText('Project'), '2')

    await waitFor(() => {
      expect(screen.getByLabelText('Project')).toHaveValue('2')
    })
    // Still on Logs, not bounced to the overview.
    expect(screen.getByRole('heading', { name: 'Logs' })).toBeInTheDocument()
  })
})

describe('Rank charts', () => {
  const PERF = {
    period: '24h',
    endpoints: [
      {
        name: '/slow',
        op: 'http.server',
        count: 5,
        throughput_per_minute: 0.1,
        failure_rate: 0,
        total_ms: 5000,
        p50: 900,
        p75: 950,
        p95: 1000,
        p99: 1100,
      },
      {
        name: '/fast',
        op: 'http.server',
        count: 500,
        throughput_per_minute: 5,
        failure_rate: 0,
        total_ms: 500,
        p50: 1,
        p75: 1,
        p95: 2,
        p99: 3,
      },
    ],
    summary: {
      transactions: 505,
      throughput_per_minute: 5.1,
      failure_rate: 0,
      series: [505],
      bucket_seconds: 3600,
    },
  }

  function renderPerf() {
    return render(
      <MemoryRouter initialEntries={['/projects/1/performance']}>
        <App />
      </MemoryRouter>,
    )
  }

  it('ranks by the same measure the table is sorted by', async () => {
    // A chart and a table that disagree about the same window is worse than either alone.
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/': { body: { ...PROJECT, keys: [] } },
      '/projects/1/performance/': { body: PERF },
    })

    renderPerf()

    expect(await screen.findByText('Top endpoints by time spent')).toBeInTheDocument()

    await userEvent.selectOptions(screen.getByLabelText('Sort by'), 'p95')
    expect(await screen.findByText('Top endpoints by p95 latency')).toBeInTheDocument()
  })

  it('says what a bar length means rather than implying it', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/': { body: { ...PROJECT, keys: [] } },
      '/projects/1/performance/': { body: PERF },
    })

    renderPerf()

    expect(
      await screen.findByText(/Bar length is time spent, relative to the highest/),
    ).toBeInTheDocument()
  })

  it('carries the window and the op through to the endpoint it links to', async () => {
    // The table groups by (name, op), so a link that names only the endpoint can land on
    // merged figures matching neither row. The period has to survive the trip too, or a 7d
    // view silently becomes a 24h one on the way back.
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/': { body: { ...PROJECT, keys: [] } },
      '/projects/1/performance/': { body: PERF },
    })

    render(
      <MemoryRouter initialEntries={['/projects/1/performance?period=7d']}>
        <App />
      </MemoryRouter>,
    )

    await screen.findByText('Top endpoints by time spent')
    const links = [...document.querySelectorAll('a.perf__link')].map((a) => a.getAttribute('href'))
    expect(links).toContain('/projects/1/endpoint?period=7d&name=%2Fslow&op=http.server')
  })

  it('keeps the latency columns in the aligned monospace figures', async () => {
    // These read as a column of numbers only while they are tabular and right-aligned. A
    // missing separator in the class attribute is invisible in review and obvious on screen.
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/': { body: { ...PROJECT, keys: [] } },
      '/projects/1/performance/': { body: PERF },
    })

    renderPerf()

    const p95 = await screen.findByText('1.00s')
    expect(p95).toHaveClass('num', 'strong')
  })

  it('scales the longest bar to full width and the rest against it', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/': { body: { ...PROJECT, keys: [] } },
      '/projects/1/performance/': { body: PERF },
    })

    renderPerf()

    await screen.findByText('Top endpoints by time spent')
    const widths = [...document.querySelectorAll('.rank__bar')].map(
      (bar) => (bar as HTMLElement).style.width,
    )
    expect(widths[0]).toBe('100%')
    // 500 against 5000 is 10% — the bar has to be honest about the ratio.
    expect(widths[1]).toBe('10%')
  })

  it('links a span bar into its detail page', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/': { body: { ...PROJECT, keys: [] } },
      '/projects/1/spans/': {
        body: {
          period: '24h',
          ops: ['db.query'],
          spans: [
            {
              op: 'db.query',
              description: 'SELECT 1',
              count: 10,
              transactions: 2,
              per_transaction: 5,
              throughput_per_minute: 0.1,
              total_ms: 100,
              p50: 9,
              p95: 12,
            },
          ],
        },
      },
    })

    render(
      <MemoryRouter initialEntries={['/projects/1/spans']}>
        <App />
      </MemoryRouter>,
    )

    await screen.findByText(/Bar length is time spent/)
    const links = screen.getAllByRole('link').map((a) => a.getAttribute('href'))
    expect(links.some((href) => href?.includes('/projects/1/span?'))).toBe(true)
  })
})

describe('Review findings', () => {
  it('switching project from a detail page does not carry the record id across', async () => {
    // /projects/1/issues/9 -> /projects/2/issues/9 would render project 1's issue under
    // project 2's header. Only the tab segment travels.
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/': { body: [PROJECT, { ...PROJECT, id: 2, name: 'Billing' }] },
      '/projects/1/': { body: { ...PROJECT, keys: [] } },
      '/projects/2/': { body: { ...PROJECT, id: 2, name: 'Billing', keys: [] } },
      '/issues/9/': {
        body: {
          issue: { ...ISSUE, fingerprint: 'f', fingerprint_components: [] },
          latest_event: null,
          tags: {},
          trace: null,
        },
      },
      '/projects/2/issues/': { body: [] },
    })

    render(
      <MemoryRouter initialEntries={['/projects/1/issues/9']}>
        <App />
      </MemoryRouter>,
    )

    await userEvent.selectOptions(await screen.findByLabelText('Project'), '2')

    // The issue stream for project 2, not issue 9 wearing project 2's header.
    expect(await screen.findByRole('heading', { name: 'Issues' })).toBeInTheDocument()
  })

  it('ranked bars navigate in-app rather than reloading the page', async () => {
    // A raw <a href> full-page-reloads an SPA. An href-only assertion passes either way, which
    // is why this asserts the navigation actually happened.
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/': { body: { ...PROJECT, keys: [] } },
      '/projects/1/spans/': {
        body: {
          period: '24h',
          ops: ['db.query'],
          spans: [
            {
              op: 'db.query',
              description: 'SELECT 1',
              count: 10,
              transactions: 2,
              per_transaction: 5,
              throughput_per_minute: 0.1,
              total_ms: 100,
              p50: 9,
              p95: 12,
            },
          ],
        },
      },
      '/projects/1/span/': {
        body: {
          op: 'db.query',
          description: 'SELECT 1',
          period: '24h',
          summary: {
            count: 10,
            transactions: 2,
            per_transaction: 5,
            total_ms: 100,
            p50: 9,
            p95: 12,
            p99: 12,
            slowest: 14,
          },
          distribution: [{ from_ms: 0, to_ms: 1, count: 10 }],
          callers: [],
          samples: [],
        },
      },
    })

    render(
      <MemoryRouter initialEntries={['/projects/1/spans']}>
        <App />
      </MemoryRouter>,
    )

    const chart = await screen.findByRole('figure')
    await userEvent.click(within(chart).getByRole('link'))

    expect(await screen.findByText('How long these calls take')).toBeInTheDocument()
  })

  it('does not stack an empty chart on top of an empty state', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/': { body: { ...PROJECT, keys: [] } },
      '/projects/1/performance/': {
        body: {
          period: '24h',
          endpoints: [],
          summary: {
            transactions: 0,
            throughput_per_minute: 0,
            failure_rate: 0,
            series: [],
            bucket_seconds: 3600,
          },
        },
      },
    })

    render(
      <MemoryRouter initialEntries={['/projects/1/performance']}>
        <App />
      </MemoryRouter>,
    )

    expect(await screen.findByText(/No transactions yet/)).toBeInTheDocument()
    expect(screen.queryByText(/Nothing to rank yet/)).not.toBeInTheDocument()
  })
})

describe('Endpoint detail', () => {
  const DETAIL = {
    name: '/checkout/{cart_id}',
    period: '24h',
    summary: {
      count: 27,
      failure_rate: 0.667,
      throughput_per_minute: 0.02,
      total_ms: 2900,
      p50: 196,
      p95: 365,
      p99: 394,
      slowest: 400,
    },
    distribution: Array.from({ length: 20 }, (_, i) => ({
      from_ms: i * 20,
      to_ms: (i + 1) * 20,
      count: i === 9 ? 20 : 0,
    })),
    spans: [
      {
        op: 'http.client',
        description: 'POST payments.example.com/charge',
        count: 27,
        total_ms: 2500,
        p95: 380,
        share: 0.86,
      },
      {
        op: 'db.query',
        description: 'SELECT * FROM carts',
        count: 27,
        total_ms: 174,
        p95: 18,
        share: 0.06,
      },
    ],
    samples: [
      {
        transaction_id: 'txn-1',
        duration_ms: 400,
        status: 'internal_error',
        trace_id: 'a'.repeat(32),
        timestamp: new Date().toISOString(),
      },
    ],
  }

  function renderEndpoint() {
    return render(
      <MemoryRouter initialEntries={['/projects/1/endpoint?name=%2Fcheckout%2F%7Bcart_id%7D']}>
        <App />
      </MemoryRouter>,
    )
  }

  it('shows where the endpoint spends its time, with each span share', async () => {
    // The share is what says whether fixing a span would move the endpoint at all.
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/': { body: { ...PROJECT, keys: [] } },
      '/projects/1/endpoint/': { body: DETAIL },
    })

    renderEndpoint()

    expect(await screen.findByText('Where its time goes')).toBeInTheDocument()
    expect(screen.getByText(/86% of this endpoint/)).toBeInTheDocument()
  })

  it('links each span onward to its own detail page', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/': { body: { ...PROJECT, keys: [] } },
      '/projects/1/endpoint/': { body: DETAIL },
    })

    renderEndpoint()

    await screen.findByText('Where its time goes')
    const links = screen.getAllByRole('link').map((a) => a.getAttribute('href'))
    expect(links.some((href) => href?.includes('/projects/1/span?'))).toBe(true)
  })

  it('labels the distribution in milliseconds, not clock time', async () => {
    // The x-axis is duration. "24h ago" here would be the wrong unit entirely.
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/': { body: { ...PROJECT, keys: [] } },
      '/projects/1/endpoint/': { body: DETAIL },
    })

    renderEndpoint()

    expect(await screen.findByText('How long its requests take')).toBeInTheDocument()

    // Scoped to the histogram specifically: the page has two figures, and 400ms is also the
    // "Slowest" value in the meta strip above them.
    const chart = screen.getByRole('img', { name: /How long 27 requests took/ }).parentElement!
    expect(within(chart).getAllByText('400ms').length).toBeGreaterThan(0)
    expect(within(chart).queryByText(/ago/)).not.toBeInTheDocument()
  })

  it('says so when nothing inside the request is instrumented', async () => {
    mockApi({
      '/me/': { body: { authenticated: true, username: 'admin' } },
      '/projects/1/': { body: { ...PROJECT, keys: [] } },
      '/projects/1/endpoint/': { body: { ...DETAIL, spans: [] } },
    })

    renderEndpoint()

    expect(
      await screen.findByText(/Nothing inside this request is instrumented/),
    ).toBeInTheDocument()
  })
})
