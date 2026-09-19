/**
 * The progress strip above an answer.
 *
 * Every row here is a real `audit` event that arrived over the stream - nothing
 * is simulated. Each record is also stamped with the moment it happened, so a
 * row carries the seconds its stage took: measured to the end if the stage is
 * over, and to now if it is not. A reloaded page reads the same records back
 * from disk and shows the same numbers, because both come from the same two
 * timestamps rather than from anything the browser counted.
 *
 * A row with something to show expands into the details of what the stage
 * actually did - which memories went in, what a compaction folded, which model
 * answered. Running stages are open; finished ones fold away, with one
 * exception: the memory step only exists when it did something, so its result
 * stays readable.
 */

import { useState } from 'react'

import { useNow } from '../hooks/useNow'
import { ChevronRightIcon } from './icons'
import { detailLines, summaryOf } from '../runs/auditDetail'
import { stageMillis, toSeconds, totalMillis } from '../runs/duration'
import type { AuditRow } from '../runs/reducer'

/** Stage names come from `backend/app/runs.py`. */
const STAGE_LABELS: Record<string, string> = {
  context_compaction: '整理上下文',
  context_retrieval: '检索上下文',
  model_stream: '生成回复',
  memory_write: '写入记忆',
  knowledge_tool: '查阅知识库',
  skill_tool: '调用技能',
  tool_rounds_exhausted: '工具轮次已达上限',
}

export function ProgressStrip({ audits }: { audits: AuditRow[] }) {
  const running = audits.some((row) => row.state === 'running')
  const now = useNow(running)
  // The user's own choice of open rows, kept apart from the default so a
  // stage finishing does not slam shut something that was opened on purpose.
  const [pinned, setPinned] = useState<Record<string, boolean>>({})

  if (audits.length === 0) {
    return null
  }

  const total = totalMillis(audits, now)

  return (
    <div className="progress-strip">
      {audits.map((row) => {
        const details = detailLines(row)
        const expandable = details.length > 0
        const open = expandable && (pinned[row.stage] ?? defaultOpen(row))
        return (
          <div key={row.stage} className="progress-item">
            <button
              type="button"
              className={`progress-row is-${row.state}${expandable ? ' is-expandable' : ''}`}
              aria-expanded={expandable ? open : undefined}
              onClick={
                expandable
                  ? () => setPinned((current) => ({ ...current, [row.stage]: !open }))
                  : undefined
              }
            >
              <span className="progress-dot" />
              {/* The arrow belongs to the name, not to the numbers: it reads
                  as "this title opens", the way a tree node does. */}
              <span className="progress-head">
                <span className="progress-label">{STAGE_LABELS[row.stage] ?? row.stage}</span>
                {expandable && (
                  <ChevronRightIcon
                    size={13}
                    className={`progress-caret${open ? ' is-open' : ''}`}
                  />
                )}
              </span>
              <span className="progress-detail">{summaryOf(row)}</span>
              <span className="progress-time">{timeOf(row, now)}</span>
            </button>
            {open && (
              <div className="progress-detail-body">
                {details.map((line, index) => (
                  <div key={index} className="progress-detail-line">
                    {line}
                  </div>
                ))}
              </div>
            )}
          </div>
        )
      })}
      {total !== null && <div className="progress-total">共 {toSeconds(total)} 秒</div>}
    </div>
  )
}

/**
 * Open while a stage is running, and for the memory step afterwards: it only
 * ever appears having done something, so what it did is the point of it.
 */
function defaultOpen(row: AuditRow): boolean {
  return row.state === 'running' || row.stage === 'memory_write'
}

/** How long the stage has taken, or nothing when it was never timed. */
function timeOf(row: AuditRow, now: number): string {
  const millis = stageMillis(row, now)
  return millis === null ? '' : `${toSeconds(millis)} 秒`
}
