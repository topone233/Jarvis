/**
 * The conversation as it should look right now.
 *
 * History comes from the API and the live turn comes from `useRun`, and the two
 * are merged by one rule: a message the live turn owns is rendered by the turn,
 * not by the history. That is what lets a reloaded page show a run in progress
 * without the partially-written answer appearing twice.
 */

import type { Message, RunEventRecord, RunStatus } from '../api/types'
import { createTurn, reduce, type TurnPhase, type TurnState } from '../runs/reducer'
import { AssistantTurn, type FeedbackKind } from './AssistantTurn'

/** The verdict of the conversation's newest run, fetched once on load. */
export interface LastRun {
  runId: string
  status: RunStatus
}

export interface MessageListProps {
  messages: Message[]
  turn: TurnState | null
  lastRun: LastRun | null
  /** Every run's audit trail in this conversation, keyed by run id. */
  trail: Record<string, RunEventRecord[]>
  gaveUp: boolean
  /** The message just sent, shown before the server has stored it. */
  pendingUser: string | null
  onReconnect(): void
  onRegenerate(messageId: string): void
  onFeedback(messageId: string, kind: FeedbackKind): void
}

export function MessageList({
  messages,
  turn,
  lastRun,
  trail,
  gaveUp,
  pendingUser,
  onReconnect,
  onRegenerate,
  onFeedback,
}: MessageListProps) {
  return (
    <>
      {messages.map((message) => {
        if (message.role === 'user') {
          return (
            <div key={message.id} className="msg-user">
              {message.content}
            </div>
          )
        }
        if (message.role !== 'assistant') {
          return null
        }
        if (turn !== null && turn.assistantMessageId === message.id) {
          return null
        }
        return (
          <AssistantTurn
            key={message.id}
            turn={turnFromMessage(message, lastRun, trail)}
            gaveUp={false}
            onReconnect={onReconnect}
            onRegenerate={onRegenerate}
            onFeedback={onFeedback}
          />
        )
      })}
      {pendingUser !== null && <div className="msg-user">{pendingUser}</div>}
      {turn !== null && (
        <AssistantTurn
          key={turn.localId}
          turn={turn}
          gaveUp={gaveUp}
          onReconnect={onReconnect}
          onRegenerate={onRegenerate}
          onFeedback={onFeedback}
        />
      )}
    </>
  )
}

/**
 * Renders a stored answer through the same component the live one uses.
 *
 * The one thing history cannot tell us on its own is whether an unfinished
 * answer was interrupted - a run killed mid-flight leaves text and a run id and
 * nothing else. That is what `lastRun` carries in, so the answer is labelled
 * honestly instead of passing for complete. The trail carries in the other
 * thing history lacks: the steps that produced the answer, which are on disk
 * long after the run that wrote them is gone.
 */
function turnFromMessage(
  message: Message,
  lastRun: LastRun | null,
  trail: Record<string, RunEventRecord[]>,
): TurnState {
  const metadata = message.metadata ?? {}
  const status = lastRun !== null && metadata.run_id === lastRun.runId ? lastRun.status : null

  let phase: TurnPhase = 'completed'
  if (metadata.cancelled) {
    phase = 'cancelled'
  } else if (status === 'interrupted' || status === 'failed' || metadata.error) {
    phase = 'failed'
  }

  const turn: TurnState = {
    ...createTurn(message.id, {
      assistantMessageId: message.id,
      runId: metadata.run_id,
      content: message.content,
      reasoning: metadata.reasoning,
    }),
    status,
    citations: metadata.citations ?? [],
    phase,
    error: metadata.error ?? null,
  }

  // Through the reducer rather than beside it, so the steps of an answer read
  // back from disk are assembled by exactly the code that assembles a live
  // one's - including how a stage's two records collapse into one row.
  const records = metadata.run_id === undefined ? undefined : trail[metadata.run_id]
  return records === undefined ? turn : reduce(turn, { type: 'audits', records })
}
