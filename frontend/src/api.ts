export interface Project {
  id: number
  name: string
  slug: string
  platform: string
  organization: string
  unresolved_count: number
}

export interface Organization {
  id: number
  name: string
  slug: string
}

export interface ProjectKey {
  id: number
  label: string
  public_key: string
  dsn: string
  is_active: boolean
  created_at: string
}

export interface ProjectDetail extends Project {
  keys: ProjectKey[]
}

export interface Issue {
  id: number
  project: number
  title: string
  culprit: string
  level: string
  status: string
  times_seen: number
  first_seen: string
  last_seen: string
  hourly: number[]
}

export interface EndpointStats {
  name: string
  op: string
  count: number
  throughput_per_minute: number
  failure_rate: number
  total_ms: number
  p50: number
  p75: number
  p95: number
  p99: number
}

export interface Performance {
  period: string
  endpoints: EndpointStats[]
  summary: {
    transactions: number
    throughput_per_minute: number
    failure_rate: number
    hourly: number[]
  }
}

export interface TraceSpan {
  span_id: string
  parent_span_id: string
  op: string
  description: string
  status: string
  start_timestamp: string
  timestamp: string
  duration_ms: number
  data: Record<string, unknown>
}

export interface TraceSummary {
  id: string
  trace_id: string
  span_id: string
  name: string
  op: string
  status: string
  start_timestamp: string
  timestamp: string
  duration_ms: number
  environment: string
  release: string
  span_count: number
}

export interface CorrelatedError {
  id: string
  issue_id: number | null
  title: string
  level: string
  timestamp: string
  span_id: string
}

export interface TraceDetail extends TraceSummary {
  spans: TraceSpan[]
  errors: CorrelatedError[]
}

export interface Frame {
  filename: string
  module: string
  function: string
  lineno: number | null
  in_app: boolean
}

export interface ExceptionValue {
  type: string
  value: string
  frames: Frame[]
}

export interface ObslyEvent {
  id: string
  timestamp: string
  received_at: string
  level: string
  platform: string
  message: string
  exception_type: string
  exception_value: string
  culprit: string
  environment: string
  release: string
  server_name: string
  trace_id: string
  span_id: string
  tags: Record<string, string>
  exception: ExceptionValue[]
  payload: Record<string, unknown>
}

export interface TagValue {
  value: string
  count: number
  percentage: number
}

export interface IssueDetail {
  issue: Issue & { fingerprint: string; fingerprint_components: string[] }
  latest_event: ObslyEvent | null
  tags: Record<string, TagValue[]>
  /** The request this error happened inside, when both were recorded. */
  trace: { id: string; name: string; duration_ms: number; status: string } | null
}

/** Thrown when the session is missing or expired, so callers can show a sign-in prompt
 *  rather than an indistinguishable "something went wrong". */
export class UnauthorizedError extends Error {
  // Subclassing Error does not set `name`, and code that branches on it silently falls
  // through to the generic path.
  override name = 'UnauthorizedError'
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`/api/0${path}`, { headers: { Accept: 'application/json' } })

  if (response.status === 401 || response.status === 403) {
    throw new UnauthorizedError('not signed in')
  }
  if (!response.ok) {
    throw new Error(`${path} returned ${response.status}`)
  }
  return (await response.json()) as T
}

/** Django sets this cookie; every unsafe method must echo it back or the request is a 403.
 *  Read at call time rather than cached — a re-login rotates the token. */
function csrfToken(): string {
  return /(?:^|;\s*)csrftoken=([^;]*)/.exec(document.cookie)?.[1] ?? ''
}

async function patch<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`/api/0${path}`, {
    method: 'PATCH',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      'X-CSRFToken': csrfToken(),
    },
    body: JSON.stringify(body),
  })

  if (response.status === 401 || response.status === 403) {
    throw new UnauthorizedError('not signed in')
  }
  if (!response.ok) {
    throw new Error(`${path} returned ${response.status}`)
  }
  return (await response.json()) as T
}

export interface Session {
  authenticated: boolean
  username: string | null
}

/** The message the server sent, so "Incorrect username or password" reaches the user instead
 *  of being flattened into a status code. */
async function post<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`/api/0${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      'X-CSRFToken': csrfToken(),
    },
    body: JSON.stringify(body),
  })

  const payload = (await response.json().catch(() => ({}))) as { detail?: string }
  if (!response.ok) {
    throw new Error(payload.detail ?? `${path} returned ${response.status}`)
  }
  return payload as T
}

export const api = {
  session: () => get<Session>('/me/'),
  login: (username: string, password: string) =>
    post<Session>('/auth/login/', { username, password }),
  logout: () => post<Session>('/auth/logout/', {}),
  projects: () => get<Project[]>('/projects/'),
  organizations: () => get<Organization[]>('/organizations/'),
  createOrganization: (name: string, slug: string) =>
    post<Organization>('/organizations/', { name, slug }),
  project: (id: number) => get<ProjectDetail>(`/projects/${id}/`),
  createProject: (body: {
    name: string
    slug: string
    platform: string
    organization_id: number
  }) => post<Project>('/projects/', body),
  createKey: (projectId: number, label: string) =>
    post<ProjectKey>(`/projects/${projectId}/keys/`, { label }),
  setKeyActive: (keyId: number, is_active: boolean) =>
    patch<ProjectKey>(`/keys/${keyId}/`, { is_active }),
  issues: (projectId: number, params: URLSearchParams) =>
    get<Issue[]>(`/projects/${projectId}/issues/?${params.toString()}`),
  issue: (id: number) => get<IssueDetail>(`/issues/${id}/`),
  traces: (projectId: number, params: URLSearchParams) =>
    get<TraceSummary[]>(`/projects/${projectId}/traces/?${params.toString()}`),
  trace: (id: string) => get<TraceDetail>(`/traces/${id}/`),
  performance: (projectId: number, period: string) =>
    get<Performance>(`/projects/${projectId}/performance/?period=${period}`),
  issueEvents: (id: number) => get<ObslyEvent[]>(`/issues/${id}/events/`),
  setIssueStatus: (id: number, status: string) => patch<Issue>(`/issues/${id}/status/`, { status }),
}
