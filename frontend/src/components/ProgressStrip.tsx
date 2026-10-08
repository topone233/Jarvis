/**
 * The progress strip above an answer.
 *
 * One header line carries the whole thing: while the run is going it says what
 * is happening and for how long, and once the run is over it folds into a
 * single sentence - 已完成，用时 32秒 - that opens on click into every step.
 * The fold follows the reader's own verdict of the moment: the user's choice
 * outranks the system's, and once they have toggled the strip themselves it
 * never moves again on its own.
 *
 * Every row inside is a real `audit` event that arrived over the stream -
 * nothing is simulated. Each record is also stamped with the moment it
 * happened, so a row carries the seconds its stage took: measured to the end
 * if the stage is over, and to now if it is not. A reloaded page reads the same
 * records back from disk and shows the same numbers, because both come from the
 * same two timestamps rather than from anything the browser counted.
 *
 * A stage can occur several times in one run - the model works one round per
 * tool call, so 生成回复 has a row per round, each carrying that round's
 * thinking in the position it happened - and every occurrence is its own row.
 * A row with something to show expands into the details of what the stage
 * actually did: which memories went in, what a compaction folded, which model
 * answered - and for a tool call, the AI's original call JSON and the result
 * text that went back to it, long text folded until asked for. The running
 * stage is the one thing held open; the moment a step is over its row folds
 * away (2026-10-08: the reader watches one step at a time, and the header
 * sentence is the record of the whole).
 */

import { useState, type ComponentType, type SVGProps } from 'react'

import { useNow } from '../hooks/useNow'
import {
  AlertTriangleIcon,
  BookIcon,
  BrainIcon,
  ChevronRightIcon,
  HelpCircleIcon,
  LayersIcon,
  MessageIcon,
  SearchIcon,
  TerminalIcon,
  WrenchIcon,
  ZapIcon,
} from './icons'
import { detailLines, summaryOf } from '../runs/auditDetail'
import { formatDuration, stageMillis, totalMillis } from '../runs/duration'
import type { AuditRow } from '../runs/reducer'

/** Stage names come from `backend/app/runs.py`. */
const STAGE_LABELS: Record<string, string> = {
  context_compaction: '整理上下文',
  context_retrieval: '检索上下文',
  model_stream: '生成回复',
  memory_write: '写入记忆',
  knowledge_tool: '查阅知识库',
  skill_tool: '调用技能',
  bash_tool: '执行命令',
  ask_user: '询问用户',
  tool_call: '工具调用',
  tool_rounds_exhausted: '工具轮次已达上限',
}

/**
 * One glyph per stage, so the trail reads by shape before the label is read
 * at all. Colour stays with the state (see `.progress-stage-icon`), the
 * drawing stays with the kind.
 */
const STAGE_ICONS: Record<string, ComponentType<SVGProps<SVGSVGElement> & { size?: number }>> = {
  context_compaction: LayersIcon,
  context_retrieval: SearchIcon,
  model_stream: MessageIcon,
  memory_write: BrainIcon,
  knowledge_tool: BookIcon,
  skill_tool: ZapIcon,
  bash_tool: TerminalIcon,
  ask_user: HelpCircleIcon,
  tool_call: WrenchIcon,
  tool_rounds_exhausted: AlertTriangleIcon,
}

/** Above this many characters a raw block clamps until the user expands it. */
const CODE_CLAMP_CHARS = 480

/** How the header sentence names the turn's ending. */
export type StripOutcome = 'running' | 'completed' | 'cancelled' | 'interrupted' | 'failed'

export function ProgressStrip({
  audits,
  liveThinking = '',
  live = false,
  outcome,
}: {
  audits: AuditRow[]
  /** The round in flight's thinking, still streaming. It renders inside the
   *  running 生成回复 row; when that row closes, its payload takes over. */
  liveThinking?: string
  /** True while this turn is still being produced. Drives the automatic fold. */
  live?: boolean
  outcome: StripOutcome
}) {
  // The user's own open/closed verdict, kept apart from the automatic one so a
  // stage finishing never slams the strip shut on somebody who opened it - and
  // a re-open never undoes somebody who closed it. Null means "no verdict yet".
  const [verdict, setVerdict] = useState<boolean | null>(null)
  // The user's choice of open rows, kept apart from the default so a stage
  // finishing does not slam shut something that was opened on purpose.
  // Keyed by the row's opening sequence: one row per occurrence now.
  const [pinned, setPinned] = useState<Record<number, boolean>>({})
  const running = audits.some((row) => row.state === 'running')
  const now = useNow(running)

  if (audits.length === 0) {
    return null
  }

  const total = totalMillis(audits, now)
  const open = verdict ?? live

  return (
    <div className="progress-strip">
      <button
        type="button"
        className="progress-strip-head"
        aria-expanded={open}
        onClick={() => setVerdict(!open)}
      >
        <span className="progress-strip-label">{headLabel(outcome, total)}</span>
        <ChevronRightIcon size={13} className={`progress-caret${open ? ' is-open' : ''}`} />
      </button>
      {open && (
        <div className="progress-rows">
          {audits.map((row) => {
            const details = detailLines(row)
            const expandable = details.length > 0
            const rowOpen = expandable && (pinned[row.sequence] ?? defaultOpen(row))
            const summary = summaryOf(row)
            return (
              <div key={row.sequence} className="progress-item">
                <button
                  type="button"
                  className={`progress-row is-${row.state}${expandable ? ' is-expandable' : ''}`}
                  aria-expanded={expandable ? rowOpen : undefined}
                  onClick={
                    expandable
                      ? () => setPinned((current) => ({ ...current, [row.sequence]: !rowOpen }))
                      : undefined
                  }
                >
                  {/* The glyph names the kind of step; its colour names the
                      state. The arrow beside the title still reads as "this
                      row opens", the way a tree node does. */}
                  <StageGlyph stage={row.stage} />
                  {/* The arrow belongs to the name, not to the numbers: it reads
                      as "this title opens", the way a tree node does. */}
                  <span className="progress-head">
                    <span className="progress-label">{STAGE_LABELS[row.stage] ?? row.stage}</span>
                    {expandable && (
                      <ChevronRightIcon
                        size={13}
                        className={`progress-caret${rowOpen ? ' is-open' : ''}`}
                      />
                    )}
                  </span>
                  <span className="progress-detail" title={summary}>
                    {summary}
                  </span>
                  <span className="progress-time">{timeOf(row, now)}</span>
                </button>
                {rowOpen && (
                  <div className="progress-detail-body">
                    {row.stage === 'model_stream' &&
                      row.state === 'running' &&
                      liveThinking !== '' && <ThinkingBlock text={liveThinking} live />}
                    {details.map((line, index) =>
                      line.kind === 'code' ? (
                        <CodeDetail key={index} label={line.label} text={line.text} />
                      ) : line.kind === 'reasoning' ? (
                        <ThinkingBlock key={index} text={line.text} />
                      ) : (
                        <div key={index} className="progress-detail-line">
                          {line.text}
                        </div>
                      ),
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

/**
 * The row's own glyph: the stage's kind of work, drawn once per known stage.
 * A stage the map does not know draws nothing rather than guessing.
 */
function StageGlyph({ stage }: { stage: string }) {
  const Glyph = STAGE_ICONS[stage]
  return Glyph === undefined ? null : <Glyph size={13} className="progress-stage-icon" />
}

/**
 * The header sentence, in the shape a reader actually says it: what the turn
 * did, and - when it is worth knowing - how long it took. Only a turn that is
 * on its way or finished normally carries a duration; a stopped or broken one
 * has its own words for why, and a number next to them would pretend the
 * ending was measured when it was cut.
 */
function headLabel(outcome: StripOutcome, total: number | null) {
  switch (outcome) {
    case 'running':
      return total === null ? (
        <>正在执行…</>
      ) : (
        <>
          正在执行，用时 <span className="progress-strip-num">{formatDuration(total)}</span>…
        </>
      )
    case 'completed':
      return total === null ? (
        <>已完成</>
      ) : (
        <>
          已完成，用时 <span className="progress-strip-num">{formatDuration(total)}</span>
        </>
      )
    case 'cancelled':
      return <>已停止</>
    case 'interrupted':
      return <>已中断</>
    case 'failed':
      return <>处理失败</>
  }
}

/**
 * One block of raw audit content - a tool call's original JSON or its result
 * text. Long text clamps to a few lines and expands on demand; short text
 * just shows.
 */
function CodeDetail({ label, text }: { label: string; text: string }) {
  const clamped = text.length > CODE_CLAMP_CHARS
  const [open, setOpen] = useState(false)
  return (
    <div className="progress-detail-code">
      <div className="progress-detail-code-head">
        <span>{label}</span>
        {clamped && (
          <button type="button" onClick={() => setOpen((current) => !current)}>
            {open ? '收起' : '展开全部'}
          </button>
        )}
      </div>
      <pre className={clamped && !open ? 'is-clamped' : ''}>{text}</pre>
    </div>
  )
}

/**
 * One round's thinking, live or recorded. This is the trail's memory of what
 * the model was thinking at that point in the run - it sits on the round's own
 * row, exactly between the tool rows it led to.
 */
function ThinkingBlock({ text, live = false }: { text: string; live?: boolean }) {
  return (
    <div className="progress-thinking">
      <span className="progress-thinking-label">{live ? '思考中…' : '思考过程'}</span>
      <div className="progress-thinking-body">{text}</div>
    </div>
  )
}

/**
 * The step in flight is the only row held open; every finished one folds away
 * at once (2026-10-08). A row the user opened themselves stays open through
 * their own `pinned` choice above, whatever its state.
 */
function defaultOpen(row: AuditRow): boolean {
  return row.state === 'running'
}

/** How long the stage has taken, or nothing when it was never timed. */
function timeOf(row: AuditRow, now: number): string {
  const millis = stageMillis(row, now)
  return millis === null ? '' : formatDuration(millis)
}
