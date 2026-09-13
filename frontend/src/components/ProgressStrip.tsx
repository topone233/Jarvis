/**
 * The progress strip above an answer.
 *
 * Every row here is a real `audit` event that arrived over the stream - nothing
 * is simulated. Each record is also stamped with the moment it happened, so a
 * row carries the seconds its stage took: measured to the end if the stage is
 * over, and to now if it is not. A reloaded page reads the same records back
 * from disk and shows the same numbers, because both come from the same two
 * timestamps rather than from anything the browser counted.
 */

import { useNow } from '../hooks/useNow'
import { stageMillis, toSeconds, totalMillis } from '../runs/duration'
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
  const running = audits.some((row) => row.state === 'running')
  const now = useNow(running)

  if (audits.length === 0) {
    return null
  }

  const total = totalMillis(audits, now)

  return (
    <div className="progress-strip">
      {audits.map((row) => (
        <div key={row.stage} className={`progress-row is-${row.state}`}>
          <span className="progress-dot" />
          <span className="progress-label">{STAGE_LABELS[row.stage] ?? row.stage}</span>
          <span className="progress-detail">{detailOf(row)}</span>
          <span className="progress-time">{timeOf(row, now)}</span>
        </div>
      ))}
      {total !== null && <div className="progress-total">共 {toSeconds(total)} 秒</div>}
    </div>
  )
}

/** How long the stage has taken, or nothing when it was never timed. */
function timeOf(row: AuditRow, now: number): string {
  const millis = stageMillis(row, now)
  return millis === null ? '' : `${toSeconds(millis)} 秒`
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
