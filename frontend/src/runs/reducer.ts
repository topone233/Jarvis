/**
 * The state of one assistant turn, driven entirely by events.
 *
 * Two rules make reconnecting safe, and both matter:
 *
 * 1. Text arrives as cumulative snapshots but deltas arrive as slices, so the
 *    first delta on a connection *replaces* and every later one appends. The
 *    server publishes one string per stream and each subscriber starts at zero,
 *    so the first delta of any connection is always the whole text so far -
 *    which is exactly why a client that attaches late, or re-attaches, ends up
 *    with the same text as one that was there from the start.
 * 2. Terminal events carry the full text and replace whatever accumulated, so
 *    arriving twice, or arriving after some deltas, converges to the same result.
 *
 * Between those, a `round.reset` says a tool round ended: the next round's
 * text and reasoning both replace this one's - each round's thinking lives on
 * its own trail row, so the live strings only carry the round in flight.
 *
 * Together those mean "I was here the whole time" and "I just reloaded" render
 * through one path, which is the point of the whole run-lifecycle design.
 */

import type { Citation, RunEventRecord, RunStatus } from '../api/types'
import type { RunEvent } from './events'

export type TurnPhase = 'connecting' | 'streaming' | 'completed' | 'cancelled' | 'failed'

export interface AuditRow {
  stage: string
  state: string
  /** The opening record's sequence: the row's identity across its records. */
  sequence: number
  payload: Record<string, unknown>
  /** When the stage began, from its `running` record. Null if it never ran. */
  startedAt: string | null
  /** When the stage reported the state it is in now. Null while it is running. */
  endedAt: string | null
}

/**
 * A run paused for something only the user can give it: an approval for a
 * bash command, or an answer to the model's question. One at a time - the
 * run is a single coroutine, so at most one thing can be waiting.
 */
export interface PendingInput {
  kind: 'bash' | 'question'
  command: string
  cwd: string
  question: string
  options: string[]
}

export interface TurnState {
  /** A key that exists before the server has told us the message's real id. */
  localId: string
  assistantMessageId: string
  runId: string | null
  /** The run record's own status. The stream reports outcomes; this says why. */
  status: RunStatus | null
  content: string
  reasoning: string
  citations: Citation[]
  audits: AuditRow[]
  /** What the run is waiting on, when it is waiting. */
  pendingInput: PendingInput | null
  phase: TurnPhase
  error: string | null
  /** True while the stream is down and a retry is pending. Orthogonal to phase. */
  detached: boolean
  awaitingContent: boolean
  awaitingReasoning: boolean
}

export type TurnAction =
  | { type: 'event'; event: RunEvent }
  | { type: 'snapshot'; status: RunStatus }
  | { type: 'audits'; records: RunEventRecord[] }
  | { type: 'detached' }
  | { type: 'attached' }

/** `content` and `reasoning` seed the turn from what is already on disk, so a
 *  run that ended while nobody was watching still shows its text. */
export function createTurn(localId: string, seed?: TurnSeed): TurnState {
  return {
    localId,
    assistantMessageId: seed?.assistantMessageId ?? '',
    runId: seed?.runId ?? null,
    status: null,
    content: seed?.content ?? '',
    reasoning: seed?.reasoning ?? '',
    citations: [],
    audits: [],
    pendingInput: null,
    phase: 'connecting',
    error: null,
    detached: false,
    awaitingContent: true,
    awaitingReasoning: true,
  }
}

export interface TurnSeed {
  runId?: string
  /** Known up front when attaching to a run found on disk, or regenerating. */
  assistantMessageId?: string
  content?: string
  reasoning?: string
}

export function reduce(state: TurnState, action: TurnAction): TurnState {
  switch (action.type) {
    case 'snapshot':
      return { ...state, status: action.status }
    case 'detached':
      return { ...state, detached: true }
    case 'attached':
      // A new connection restarts the snapshot, so the next delta for each
      // stream replaces again rather than appending to what we already have.
      return { ...state, detached: false, awaitingContent: true, awaitingReasoning: true }
    case 'audits':
      return action.records.reduce((next, record) => upsertAudit(next, record), state)
    case 'event':
      return applyEvent(state, action.event)
  }
}

function applyEvent(state: TurnState, event: RunEvent): TurnState {
  switch (event.type) {
    case 'run.started':
      return {
        ...state,
        runId: event.runId || state.runId,
        assistantMessageId: event.assistantMessageId || state.assistantMessageId,
      }

    case 'message.delta':
      if (!forThisMessage(state, event.messageId) || isTerminal(state.phase)) {
        return state
      }
      return {
        ...state,
        content: state.awaitingContent ? event.delta : state.content + event.delta,
        awaitingContent: false,
        phase: 'streaming',
      }

    case 'reasoning.delta':
      if (!forThisMessage(state, event.messageId) || isTerminal(state.phase)) {
        return state
      }
      return {
        ...state,
        reasoning: state.awaitingReasoning ? event.delta : state.reasoning + event.delta,
        awaitingReasoning: false,
      }

    case 'round.reset':
      // A tool round ended and the next round's text and thinking replace
      // this one's. Reasoning resets with the text because each round's
      // thinking is recorded on its own trail row - the live string only
      // ever carries the round in flight. (The server's metadata.reasoning
      // still accumulates across rounds; that is what a reload seeds from.)
      if (!forThisMessage(state, event.messageId) || isTerminal(state.phase)) {
        return state
      }
      return {
        ...state,
        content: '',
        awaitingContent: true,
        reasoning: '',
        awaitingReasoning: true,
      }

    case 'message.completed':
      if (!forThisMessage(state, event.messageId)) {
        return state
      }
      return {
        ...state,
        content: event.content,
        // A replayed run sends no reasoning deltas, so the final metadata is
        // the only place its thinking can come from.
        reasoning: event.metadata.reasoning || state.reasoning,
        citations: event.metadata.citations ?? state.citations,
        pendingInput: null,
        awaitingContent: false,
        awaitingReasoning: false,
        phase: 'completed',
        error: null,
      }

    case 'run.cancelled':
      if (!forThisMessage(state, event.messageId)) {
        return state
      }
      return {
        ...state,
        content: event.content,
        pendingInput: null,
        phase: 'cancelled',
        error: null,
      }

    case 'run.failed':
      // Deliberately does not touch the text: whatever arrived before the
      // failure is what the user should keep seeing.
      return { ...state, pendingInput: null, phase: 'failed', error: event.error }

    case 'context.ready':
      // Only the citations are kept. The event also estimates the tokens left in
      // the window, but nothing on screen asks for that any more.
      return { ...state, citations: event.citations }

    case 'audit': {
      const next = upsertAudit(state, event.record)
      // A pending request dies with the stage it belongs to: the closing
      // record of bash_tool or ask_user is the server saying the wait is
      // over, however it ended. Only one stage can be waiting at a time,
      // so either closing record clears it.
      if (
        next.pendingInput !== null &&
        event.record.state !== 'running' &&
        (event.record.stage === 'bash_tool' || event.record.stage === 'ask_user')
      ) {
        return { ...next, pendingInput: null }
      }
      return next
    }

    case 'user_input.requested':
      if (isTerminal(state.phase)) {
        return state
      }
      return {
        ...state,
        pendingInput: {
          kind: event.kind,
          command: event.command,
          cwd: event.cwd,
          question: event.question,
          options: event.options,
        },
      }

    case 'ignored':
      return state
  }
}

/**
 * One row per occurrence, not per stage: a stage can run several times in a
 * single run - each knowledge round is its own tool call - and collapsing them
 * into one row would hide every step but the last, which is exactly the part a
 * reload can least afford to lose. A `running` record always opens a row; an
 * ending record closes the stage's most recent open row, or opens its own when
 * it has none (a `/skill` step is announced completed, without a run-up).
 *
 * Audits keep arriving after the answer is complete - the memory write is
 * reported once the text is already on screen - so they are never gated on the
 * phase. Only text stops at a terminal event.
 */
function upsertAudit(state: TurnState, record: RunEventRecord): TurnState {
  let index = -1
  if (record.state !== 'running') {
    for (let i = state.audits.length - 1; i >= 0; i -= 1) {
      if (state.audits[i].stage === record.stage && state.audits[i].state === 'running') {
        index = i
        break
      }
    }
  }
  const previous = index === -1 ? null : state.audits[index]
  const running = record.state === 'running'
  const row: AuditRow = {
    stage: record.stage,
    state: record.state,
    // The row's identity is the record that opened it - stable across the
    // records that close it, and unique across occurrences of the stage.
    sequence: previous?.sequence ?? record.sequence,
    // Merged rather than replaced: the model's name is announced in the
    // `running` record and its token usage in the `completed` one, and only
    // the union of the two is the whole truth about the stage. The newer
    // record wins on a clash.
    payload: { ...(previous?.payload ?? {}), ...(record.payload ?? {}) },
    // A stage arrives as two records - it began, then it ended - and the second
    // replaces the first. The start is carried across, because the distance
    // between the two is the duration and the later record alone only knows
    // when the stage stopped.
    startedAt: running ? record.created_at : (previous?.startedAt ?? null),
    endedAt: running ? null : record.created_at,
  }
  if (previous === null) {
    return { ...state, audits: [...state.audits, row] }
  }
  const audits = state.audits.slice()
  audits[index] = row
  return { ...state, audits }
}

function isTerminal(phase: TurnPhase): boolean {
  return phase === 'completed' || phase === 'cancelled' || phase === 'failed'
}

/** Before `run.started` lands the id is unknown, so anything is accepted. */
function forThisMessage(state: TurnState, messageId: string): boolean {
  return (
    state.assistantMessageId === '' || messageId === '' || messageId === state.assistantMessageId
  )
}
