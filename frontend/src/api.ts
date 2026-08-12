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

export interface PerformanceEvidence {
  description: string
  op: string
  repeat_count: number
  total_ms: number
  wasted_ms: number
  transaction: string
  trace_id: string
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
  /** Where this bug came from, captured once when the issue was created. */
  first_release: string
  category: 'error' | 'performance'
  issue_type: string
  evidence: PerformanceEvidence | Record<string, never>
}

export interface SpanStats {
  op: string
  description: string
  count: number
  transactions: number
  per_transaction: number
  throughput_per_minute: number
  total_ms: number
  p50: number
  p95: number
}

export interface EndpointDetail {
  name: string
  period: string
  summary: {
    count: number
    failure_rate: number
    throughput_per_minute: number
    total_ms: number
    p50: number
    p95: number
    p99: number
    slowest: number
  }
  distribution: { from_ms: number; to_ms: number; count: number }[]
  spans: {
    op: string
    description: string
    count: number
    total_ms: number
    p95: number
    share: number
  }[]
  samples: {
    transaction_id: string
    duration_ms: number
    status: string
    trace_id: string
    timestamp: string
  }[]
}

export interface SpanDetail {
  op: string
  description: string
  period: string
  summary: {
    count: number
    transactions: number
    per_transaction: number
    total_ms: number
    p50: number
    p95: number
    p99: number
    slowest: number
  }
  distribution: { from_ms: number; to_ms: number; count: number }[]
  callers: { transaction: string; count: number; total_ms: number }[]
  samples: {
    duration_ms: number
    trace_id: string
    transaction_id: string
    transaction: string
    transaction_ms: number
    timestamp: string
  }[]
}

export interface SpanInsights {
  period: string
  ops: string[]
  spans: SpanStats[]
}

export interface Dashboard {
  period: string
  buckets: number
  bucket_seconds: number
  /** ISO time of the first bucket. Every other point is this plus n × bucket_seconds — one
   *  timestamp instead of one per point, saying the same thing. */
  series_start: string
  headline: {
    transactions: number
    throughput_per_minute: number
    failure_rate: number
    p95_ms: number
    errors: number
    unresolved_issues: number
    logs: number
  }
  series: {
    throughput: number[]
    failures: number[]
    errors: number[]
    logs: number[]
    p95: number[]
  }
  top_issues: {
    id: number
    title: string
    culprit: string
    level: string
    times_seen: number
    last_seen: string
  }[]
  slowest_endpoints: { name: string; count: number; p95: number }[]
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
    series: number[]
    bucket_seconds: number
    series_start: string
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

export interface LogRecord {
  id: string
  timestamp: string
  level: string
  body: string
  logger: string
  trace_id: string
  span_id: string
  environment: string
  release: string
  attributes: Record<string, unknown>
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
  logs: LogRecord[]
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

export interface Release {
  version: string
  requests: number
  /** Named for what it measures: requests, not sessions. See the backend module note. */
  failure_free_rate: number
  failures: number
  p95: number
  errors: number
  issues_introduced: number
  issues_unresolved: number
  adoption: number
  first_seen: string
  last_seen: string
}

export interface WebVitals {
  period: string
  pageloads: number
  vitals: {
    key: string
    label: string
    explains: string
    /** null when nobody reported it — which is not the same as a passing score. */
    value: number | null
    samples: number
    rating: 'good' | 'needs-improvement' | 'poor' | 'none'
    good_below: number
    poor_above: number
    /** Empty for CLS: it is a ratio, and labelling it ms would be a lie. */
    unit: string
  }[]
  pages: {
    name: string
    count: number
    lcp: number | null
    cls: number | null
    inp: number | null
    rating: string
  }[]
}

export type AlertTrigger = 'new_issue' | 'regression' | 'frequency'

export interface AlertRule {
  id: number
  name: string
  trigger: AlertTrigger
  trigger_label: string
  threshold: number
  window_minutes: number
  level: string
  webhook_url: string
  cooldown_minutes: number
  enabled: boolean
  created_at: string
  /** So the page can say whether a rule has ever done anything — a rules list that cannot
   *  is one that hides its own misconfiguration. */
  fire_count: number
  last_fired_at: string | null
}

export interface AlertFire {
  id: number
  rule_name: string
  issue: number
  issue_title: string
  issue_level: string
  reason: string
  delivery: 'pending' | 'sent' | 'failed'
  status_code: number | null
  error: string
  created_at: string
}

export interface IssueDetail {
  issue: Issue & {
    fingerprint: string
    fingerprint_components: string[]
    /** Detail only — the stream draws the same counts without an axis to label. */
    hourly_start: string
    bucket_seconds: number
  }
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

/**
 * Every write, one function.
 *
 * PATCH and POST were separate copies that had drifted: one surfaced the server's `detail`
 * message and the other flattened it into a status code, so the same validation error read
 * differently depending on which verb hit it.
 */
async function send<T>(
  path: string,
  method: string,
  body?: unknown,
  // A 401 usually means the session ended. On the sign-in request it means these credentials
  // are wrong — the user has no session to lose — and flattening that into "not signed in"
  // hides the only message that helps them.
  options: { authIsCredentialError?: boolean } = {},
): Promise<T> {
  const response = await fetch(`/api/0${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      'X-CSRFToken': csrfToken(),
    },
    // Spread rather than `body: undefined` — under exactOptionalPropertyTypes those are
    // different things, and a DELETE genuinely has no body rather than an empty one.
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })

  if ((response.status === 401 || response.status === 403) && !options.authIsCredentialError) {
    throw new UnauthorizedError('not signed in')
  }

  // A 204 has no body to parse, and DELETE is the common case.
  const payload = (await response.json().catch(() => ({}))) as { detail?: string }
  if (!response.ok) {
    throw new Error(payload.detail ?? `${path} returned ${response.status}`)
  }
  return payload as T
}

const patch = <T>(path: string, body: unknown) => send<T>(path, 'PATCH', body)

export interface Session {
  authenticated: boolean
  username: string | null
}

/** The message the server sent, so "Incorrect username or password" reaches the user instead
 *  of being flattened into a status code. */
const post = <T>(path: string, body: unknown) => send<T>(path, 'POST', body)

export const api = {
  session: () => get<Session>('/me/'),
  login: (username: string, password: string) =>
    send<Session>('/auth/login/', 'POST', { username, password }, { authIsCredentialError: true }),
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
  alertRules: (projectId: number) => get<AlertRule[]>(`/projects/${projectId}/alert-rules/`),
  createAlertRule: (projectId: number, rule: Partial<AlertRule>) =>
    send<AlertRule>(`/projects/${projectId}/alert-rules/`, 'POST', rule),
  updateAlertRule: (id: number, patch: Partial<AlertRule>) =>
    send<AlertRule>(`/alert-rules/${id}/`, 'PATCH', patch),
  deleteAlertRule: (id: number) => send<null>(`/alert-rules/${id}/`, 'DELETE'),
  testAlertRule: (id: number) => send<AlertFire>(`/alert-rules/${id}/test/`, 'POST'),
  alerts: (projectId: number) => get<AlertFire[]>(`/projects/${projectId}/alerts/`),
  vitals: (projectId: number, period: string) =>
    get<WebVitals>(`/projects/${projectId}/vitals/?period=${period}`),
  releases: (projectId: number, period: string) =>
    get<{ releases: Release[] }>(`/projects/${projectId}/releases/?period=${period}`).then(
      (body) => body.releases,
    ),
  traces: (projectId: number, params: URLSearchParams) =>
    get<TraceSummary[]>(`/projects/${projectId}/traces/?${params.toString()}`),
  trace: (id: string) => get<TraceDetail>(`/traces/${id}/`),
  logs: (projectId: number, params: URLSearchParams) =>
    get<LogRecord[]>(`/projects/${projectId}/logs/?${params.toString()}`),
  dashboard: (projectId: number, period: string) =>
    get<Dashboard>(`/projects/${projectId}/dashboard/?period=${period}`),
  endpointDetail: (projectId: number, period: string, name: string, op: string) =>
    get<EndpointDetail>(
      `/projects/${projectId}/endpoint/?period=${period}&name=${encodeURIComponent(name)}` +
        `&op=${encodeURIComponent(op)}`,
    ),
  spanDetail: (projectId: number, period: string, op: string, description: string) =>
    get<SpanDetail>(
      `/projects/${projectId}/span/?period=${period}&op=${encodeURIComponent(op)}` +
        `&description=${encodeURIComponent(description)}`,
    ),
  spans: (projectId: number, period: string, op: string) =>
    get<SpanInsights>(`/projects/${projectId}/spans/?period=${period}&op=${op}`),
  performance: (projectId: number, period: string) =>
    get<Performance>(`/projects/${projectId}/performance/?period=${period}`),
  issueEvents: (id: number) => get<ObslyEvent[]>(`/issues/${id}/events/`),
  setIssueStatus: (id: number, status: string) => patch<Issue>(`/issues/${id}/status/`, { status }),
}
