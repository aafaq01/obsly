import { Link } from 'react-router-dom'

import type { LogRecord } from '../api'

interface Props {
  records: LogRecord[]
  /** When given, each line offers to filter the log view down to its own request. */
  projectId?: number
}

/**
 * A log stream.
 *
 * Monospace, one line per record. The value of a log viewer is scanning a hundred lines
 * quickly, so anything that makes a line taller makes the viewer worse.
 */
export function LogList({ records, projectId }: Props) {
  if (records.length === 0) {
    return <p className="logs__empty">No log records.</p>
  }

  return (
    <div className="logs">
      {records.map((record) => (
        <div className="log" key={record.id}>
          <time className="log__time">{new Date(record.timestamp).toLocaleTimeString()}</time>
          {/* Level is a word first; the tint is a second channel, never the only one. */}
          <span className={`log__level log__level--${record.level}`}>{record.level}</span>
          {record.logger && <span className="log__logger">{record.logger}</span>}
          <span className="log__body">{record.body}</span>
          {projectId !== undefined && record.trace_id && (
            <Link
              className="log__trace"
              to={`/projects/${projectId}/logs?trace_id=${record.trace_id}`}
              title={record.trace_id}
            >
              this request
            </Link>
          )}
        </div>
      ))}
    </div>
  )
}
