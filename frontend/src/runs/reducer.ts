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
 * Together those mean "I was here the whole time" and "I just reloaded" render
 * through one path, which is the point of the whole run-lifecycle design.
 */

import type { Citation, RunEventRecord, RunStatus } from '../api/types'
import type { RunEvent } from './events'

export type TurnPhase = 'connecting' | 'streaming' | 'completed' | 'cancelled' | 'failed'

export interface AuditRow {
  stage: string
  state: string
  sequence: number
  payload: Record<string, unknown>
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
  remainingTokens: number | null
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
    remainingTokens: null,
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
        awaitingContent: false,
        awaitingReasoning: false,
        phase: 'completed',
        error: null,
      }

    case 'run.cancelled':
      if (!forThisMessage(state, event.messageId)) {
        return state
      }
      return { ...state, content: event.content, phase: 'cancelled', error: null }

    case 'run.failed':
      // Deliberately does not touch the text: whatever arrived before the
      // failure is what the user should keep seeing.
      return { ...state, phase: 'failed', error: event.error }

    case 'context.ready':
      return { ...state, citations: event.citations, remainingTokens: event.remainingTokens }

    case 'audit':
      return upsertAudit(state, event.record)

    case 'ignored':
      return state
  }
}

/**
 * Audits keep arriving after the answer is complete - the memory write is
 * reported once the text is already on screen - so they are never gated on the
 * phase. Only text stops at a terminal event.
 */
function upsertAudit(state: TurnState, record: RunEventRecord): TurnState {
  const row: AuditRow = {
    stage: record.stage,
    state: record.state,
    sequence: record.sequence,
    payload: record.payload ?? {},
  }
  const index = state.audits.findIndex((existing) => existing.stage === row.stage)
  if (index === -1) {
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
