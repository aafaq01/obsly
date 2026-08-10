import { render, screen, waitFor } from '@testing-library/react'
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
}

function renderApp() {
  return render(
    <MemoryRouter initialEntries={['/']}>
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

  it('shows a count on hover', async () => {
    render(<EventChart hourly={[0, 5]} />)

    const targets = document.querySelectorAll('rect[fill="transparent"]')
    await userEvent.hover(targets[1] as Element)

    expect(await screen.findByRole('status')).toHaveTextContent('5 events')
  })

  it('renders a baseline tick for an empty hour', () => {
    // Otherwise "no events this hour" is indistinguishable from "the chart failed to draw".
    render(<EventChart hourly={[0, 0, 0]} />)

    expect(document.querySelectorAll('.chart__bar--empty')).toHaveLength(3)
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
