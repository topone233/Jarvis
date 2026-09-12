/**
 * The progress strip above an answer.
 *
 * Every row here is a real `audit` event that arrived over the stream - nothing
 * is simulated and nothing is timed. On a reload the history is backfilled from
 * `GET /api/runs/{id}/events`, so a run watched from halfway through still shows
 * what happened before the browser arrived.
 */

import type { AuditRow } from '../runs/reducer'

/** Stage names come from `backend/app/runs.py`. */
const STAGE_LABELS: Record<string, string> = {
  context_compaction: '整理上下文',
  context_retrieval: '检索上下文',
  model_stream: '生成回复',
  memory_write: '写入记忆',
}

const STATE_LABELS: Record<string, string> = {
  running: '',
  completed: '',
  cancelled: '已停止',
  failed: '失败',
  skipped: '已跳过',
}

export function ProgressStrip({ audits }: { audits: AuditRow[] }) {
  if (audits.length === 0) {
    return null
  }
  return (
    <div className="progress-strip">
      {audits.map((row) => (
        <div key={row.stage} className={`progress-row is-${row.state}`}>
          <span className="progress-dot" />
          <span className="progress-label">{STAGE_LABELS[row.stage] ?? row.stage}</span>
          <span className="progress-detail">{detailOf(row)}</span>
        </div>
      ))}
    </div>
  )
}

/** Whatever the backend put in the payload that is worth a glance. */
function detailOf(row: AuditRow): string {
  const parts: string[] = []
  const stateNote = STATE_LABELS[row.state]
  if (stateNote) {
    parts.push(stateNote)
  }

  const payload = row.payload ?? {}
  const count = payload.count
  if (typeof count === 'number' && count > 0) {
    parts.push(`${count} 条`)
  }
  const citationCount = payload.citation_count
  if (typeof citationCount === 'number' && citationCount > 0) {
    parts.push(`引用 ${citationCount} 条`)
  }
  const reason = payload.reason
  if (typeof reason === 'string' && reason !== '') {
    parts.push(reason)
  }

  return parts.join(' · ')
}
