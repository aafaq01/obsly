import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { Select } from '../components/Select'

import { api, type AlertFire, type AlertRule, type AlertTrigger } from '../api'
import { Notice, Skeleton } from '../components/Notice'
import { handle } from '../errors'
import { absoluteTime, relativeTime } from '../time'

const TRIGGERS: { value: AlertTrigger; label: string; explains: string }[] = [
  {
    value: 'new_issue',
    label: 'A new issue appears',
    explains: 'Fires the first time a bug is seen — the one nobody has triaged yet.',
  },
  {
    value: 'regression',
    label: 'A resolved issue happens again',
    explains: 'The case a "new issue" rule misses: the issue is not new, it came back.',
  },
  {
    value: 'frequency',
    label: 'An issue crosses a rate',
    explains: 'For the bug that was tolerable at 5 an hour and is not at 500.',
  },
]

/**
 * Alert rules, and what they have actually done.
 *
 * Everything else in Obsly is pull — you open a page and ask a question. This is the one push,
 * and the only part that has to work while nobody is looking.
 *
 * The fired-alerts feed sits on the same page as the rules deliberately: a rules list with no
 * evidence of firing is how a broken webhook stays broken until the incident it was meant to
 * warn about.
 */
export function Alerts() {
  const { projectId } = useParams()
  const id = Number(projectId)

  const [rules, setRules] = useState<AlertRule[] | null>(null)
  const [fires, setFires] = useState<AlertFire[]>([])
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  const reload = () => {
    api.alertRules(id).then(setRules).catch(handle(setError))
    api.alerts(id).then(setFires).catch(handle(setError))
  }

  useEffect(reload, [id])

  const act = (work: Promise<unknown>, done: string) => {
    setNote(null)
    work
      .then(() => {
        setNote(done)
        reload()
      })
      .catch((cause: unknown) => setNote(cause instanceof Error ? cause.message : 'Failed'))
  }

  if (error) return <Notice>{error}</Notice>
  if (!rules) return <Skeleton rows={4} />

  return (
    <>
      <div className="page-head">
        <h1>Alerts</h1>
        <p className="page-head__sub">
          Obsly answers questions when you open it. A rule is how it tells you without being asked.
        </p>
      </div>

      {note && (
        <p className="notice notice--inline" role="status">
          {note}
        </p>
      )}

      <div className="section">
        <h2 className="section__title">Rules</h2>
        {rules.length === 0 ? (
          <div className="card card--tight">
            <p className="logs__empty">
              No rules, so nothing here will ever reach you. Add one below.
            </p>
          </div>
        ) : (
          <div className="card">
            {rules.map((rule) => (
              <RuleRow key={rule.id} rule={rule} act={act} />
            ))}
          </div>
        )}
      </div>

      <NewRule projectId={id} act={act} />

      <div className="section">
        <h2 className="section__title">Recently fired</h2>
        <div className="card">
          {fires.length === 0 ? (
            <p className="logs__empty">
              Nothing has fired yet. That is only good news if a rule above has been tested — send
              one to check the webhook works before you need it to.
            </p>
          ) : (
            fires.map((fire) => <FireRow key={fire.id} fire={fire} />)
          )}
        </div>
      </div>
    </>
  )
}

function RuleRow({
  rule,
  act,
}: {
  rule: AlertRule
  act: (work: Promise<unknown>, done: string) => void
}) {
  return (
    <div className="rule">
      <div className="rule__main">
        <span className="rule__name">
          {rule.name}
          {!rule.enabled && <span className="tag tag--muted">disabled</span>}
        </span>
        <span className="rule__detail">
          {rule.trigger_label}
          {rule.trigger === 'frequency' && ` · ${rule.threshold} events in ${rule.window_minutes}m`}
          {rule.level && ` · ${rule.level} only`}
          {` · cooldown ${rule.cooldown_minutes}m`}
        </span>
        <span className="rule__detail mono">{rule.webhook_url}</span>
      </div>

      <div className="rule__stats">
        {/* The number that says whether this rule has ever done anything. */}
        <span className="num">{rule.fire_count.toLocaleString()}</span>
        <em>
          {rule.last_fired_at ? `last ${relativeTime(rule.last_fired_at)} ago` : 'never fired'}
        </em>
      </div>

      <div className="rule__actions">
        <button onClick={() => act(api.testAlertRule(rule.id), `Sent a test to ${rule.name}.`)}>
          Send test
        </button>
        <button
          onClick={() =>
            act(
              api.updateAlertRule(rule.id, { enabled: !rule.enabled }),
              `${rule.name} ${rule.enabled ? 'disabled' : 'enabled'}.`,
            )
          }
        >
          {rule.enabled ? 'Disable' : 'Enable'}
        </button>
        <button
          className="danger"
          onClick={() => act(api.deleteAlertRule(rule.id), `Deleted ${rule.name}.`)}
        >
          Delete
        </button>
      </div>
    </div>
  )
}

function NewRule({
  projectId,
  act,
}: {
  projectId: number
  act: (work: Promise<unknown>, done: string) => void
}) {
  const [name, setName] = useState('')
  const [trigger, setTrigger] = useState<AlertTrigger>('new_issue')
  const [webhook, setWebhook] = useState('')
  const [level, setLevel] = useState('')
  const [threshold, setThreshold] = useState(25)
  const [windowMinutes, setWindowMinutes] = useState(5)
  const [cooldown, setCooldown] = useState(30)

  const chosen = TRIGGERS.find((option) => option.value === trigger)

  return (
    <div className="section">
      <h2 className="section__title">Add a rule</h2>
      <form
        className="card card--tight rule-form"
        onSubmit={(event) => {
          event.preventDefault()
          act(
            api.createAlertRule(projectId, {
              name,
              trigger,
              webhook_url: webhook,
              level,
              threshold,
              window_minutes: windowMinutes,
              cooldown_minutes: cooldown,
            }),
            `Created ${name}.`,
          )
          setName('')
          setWebhook('')
        }}
      >
        <label>
          Name
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Page the on-call"
            required
          />
        </label>

        <label>
          Trigger
          <Select
            value={trigger}
            onChange={(event) => setTrigger(event.target.value as AlertTrigger)}
          >
            {TRIGGERS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </Select>
        </label>

        {/* What the rule will do, in a sentence, before it is saved. */}
        <p className="rule-form__explains">{chosen?.explains}</p>

        {trigger === 'frequency' && (
          <div className="rule-form__pair">
            <label>
              Events
              <input
                type="number"
                min={1}
                value={threshold}
                onChange={(event) => setThreshold(Number(event.target.value))}
              />
            </label>
            <label>
              Within (minutes)
              <input
                type="number"
                min={1}
                value={windowMinutes}
                onChange={(event) => setWindowMinutes(Number(event.target.value))}
              />
            </label>
          </div>
        )}

        <label>
          Only this level
          <Select value={level} onChange={(event) => setLevel(event.target.value)}>
            <option value="">Any level</option>
            <option value="fatal">fatal</option>
            <option value="error">error</option>
            <option value="warning">warning</option>
          </Select>
        </label>

        <label>
          Cooldown (minutes)
          <input
            type="number"
            min={0}
            value={cooldown}
            onChange={(event) => setCooldown(Number(event.target.value))}
          />
          <small>
            How long the same issue stays quiet after firing. Without one, a bug seen a thousand
            times an hour sends a thousand notifications and the channel gets muted.
          </small>
        </label>

        <label>
          Webhook URL
          <input
            type="url"
            value={webhook}
            onChange={(event) => setWebhook(event.target.value)}
            placeholder="https://hooks.slack.com/services/..."
            required
          />
          <small>
            Slack, Discord, Teams, PagerDuty and Opsgenie all accept an incoming webhook. Obsly
            POSTs JSON to it.
          </small>
        </label>

        <button type="submit" className="primary">
          Create rule
        </button>
      </form>
    </div>
  )
}

function FireRow({ fire }: { fire: AlertFire }) {
  return (
    <Link className="mini-row" to={`/issues/${fire.issue}`}>
      <span className="mini-row__main">
        <span className={`level level--${fire.issue_level}`}>{fire.issue_level}</span>
        <span className="mini-row__title">{fire.issue_title}</span>
        <span className="rule__detail">
          {fire.rule_name} · {fire.reason}
        </span>
      </span>
      <span className="mini-row__num">
        {/* A failed delivery is the whole reason this feed exists: "we were not told" and
            "nothing happened" must not look the same. */}
        <span className={`tag tag--${fire.delivery}`}>
          {fire.delivery}
          {fire.status_code ? ` ${fire.status_code}` : ''}
        </span>
        <em title={absoluteTime(fire.created_at)}>{relativeTime(fire.created_at)} ago</em>
      </span>
    </Link>
  )
}
