/**
 * Reads an SSE response body as a sequence of Jarvis events.
 *
 * `fetch` rather than `EventSource`, because the endpoint that starts a run
 * takes a POST body and `EventSource` can only issue a bare GET. Using fetch
 * also means one code path serves both starting a run and reattaching to one,
 * and gives us the abort signal that cancellation needs.
 */

import { toApiError, withJsonBody } from '../api/client'
import { createSseParser } from './parse'
import { decodeFrame, type RunEvent } from '../runs/events'

export async function* streamRun(
  path: string,
  init: RequestInit,
  signal: AbortSignal,
): AsyncGenerator<RunEvent> {
  const response = await fetch(path, {
    ...withJsonBody(init),
    headers: { Accept: 'text/event-stream', ...init.headers },
    signal,
  })
  if (!response.ok) {
    throw await toApiError(response)
  }
  if (response.body === null) {
    throw new Error('服务没有返回流式内容。')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  const parser = createSseParser()
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) {
        return
      }
      // `stream: true` keeps a multi-byte character that straddles two network
      // chunks from being decoded as two broken halves. With Chinese text that
      // is not a corner case, it happens constantly.
      for (const frame of parser.push(decoder.decode(value, { stream: true }))) {
        yield decodeFrame(frame)
      }
    }
  } finally {
    // Covers the consumer breaking out early: without this the connection would
    // stay open and the server would keep a subscriber that nobody reads.
    void reader.cancel().catch(() => undefined)
  }
}

/** Events that end a run. Audits keep coming after these, so the caller must
 *  keep reading until the stream itself closes. */
export function isTerminalEvent(event: RunEvent): boolean {
  return (
    event.type === 'message.completed' ||
    event.type === 'run.cancelled' ||
    event.type === 'run.failed'
  )
}
