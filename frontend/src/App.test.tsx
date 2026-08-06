import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { App } from './App'

function mockFetch(response: Partial<Response> | Error) {
  const fetchMock = vi.fn(() =>
    response instanceof Error ? Promise.reject(response) : Promise.resolve(response as Response),
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('App', () => {
  it('shows a loading state before the backend answers', () => {
    mockFetch({ ok: true, status: 200, json: () => new Promise(() => {}) })

    render(<App />)

    expect(screen.getByRole('status')).toHaveTextContent('Checking backend')
  })

  it('reports backend and database status once health resolves', async () => {
    mockFetch({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ status: 'ok', database: 'ok' }),
    })

    render(<App />)

    expect(await screen.findByText(/Backend ok, database ok/)).toBeInTheDocument()
  })

  it('surfaces a non-2xx response as an error rather than rendering nothing', async () => {
    mockFetch({ ok: false, status: 503, json: () => Promise.resolve({}) })

    render(<App />)

    expect(await screen.findByRole('alert')).toHaveTextContent('backend returned 503')
  })

  it('surfaces a network failure', async () => {
    mockFetch(new Error('Failed to fetch'))

    render(<App />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Failed to fetch')
  })

  it('ignores an abort caused by unmounting', async () => {
    const abortError = new Error('aborted')
    abortError.name = 'AbortError'
    mockFetch(abortError)

    render(<App />)

    // Still loading — an abort must not be rendered as a backend failure.
    expect(await screen.findByRole('status')).toHaveTextContent('Checking backend')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
