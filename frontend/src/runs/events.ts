/**
 * Decodes SSE frames into the events this app understands.
 *
 * A frame whose payload is not valid JSON, or is not shaped as expected, is
 * dropped rather than thrown: one malformed frame must not tear down a stream
 * that is otherwise fine.
 */

import type { Citation, MessageMetadata, RunEventRecord } from '../api/types'
import type { SseFrame } from '../sse/parse'

export type RunEvent =
  | { type: 'run.started'; runId: string; assistantMessageId: string }
  | { type: 'context.ready'; citations: Citation[]; remainingTokens: number }
  | { type: 'audit'; record: RunEventRecord }
  | { type: 'message.delta'; messageId: string; delta: string }
  | { type: 'reasoning.delta'; messageId: string; delta: string }
  | { type: 'message.completed'; messageId: string; content: string; metadata: MessageMetadata }
  | { type: 'run.cancelled'; messageId: string; content: string }
  | { type: 'run.failed'; error: string }
  | { type: 'ignored'; name: string }

export function decodeFrame(frame: SseFrame): RunEvent {
  const payload = parseObject(frame.data)
  if (payload === null) {
    return { type: 'ignored', name: frame.event }
  }
  switch (frame.event) {
    case 'run.started':
      return {
        type: 'run.started',
        runId: text(payload.run_id),
        assistantMessageId: text(payload.assistant_message_id),
      }
    case 'context.ready':
      return {
        type: 'context.ready',
        citations: Array.isArray(payload.citations) ? (payload.citations as Citation[]) : [],
        remainingTokens: number(payload.remaining_token_estimate),
      }
    case 'audit':
      return { type: 'audit', record: payload as unknown as RunEventRecord }
    case 'message.delta':
      return {
        type: 'message.delta',
        messageId: text(payload.message_id),
        delta: text(payload.delta),
      }
    case 'reasoning.delta':
      return {
        type: 'reasoning.delta',
        messageId: text(payload.message_id),
        delta: text(payload.delta),
      }
    case 'message.completed':
      return {
        type: 'message.completed',
        messageId: text(payload.message_id),
        content: text(payload.content),
        metadata: (payload.metadata ?? {}) as MessageMetadata,
      }
    case 'run.cancelled':
      return {
        type: 'run.cancelled',
        messageId: text(payload.message_id),
        content: text(payload.content),
      }
    case 'run.failed':
      return { type: 'run.failed', error: text(payload.error) }
    default:
      return { type: 'ignored', name: frame.event }
  }
}

function parseObject(data: string): Record<string, unknown> | null {
  let value: unknown
  try {
    value = JSON.parse(data)
  } catch {
    return null
  }
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : null
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function number(value: unknown): number {
  return typeof value === 'number' ? value : 0
}
