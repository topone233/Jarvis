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

/**
 * How long the stream may say nothing before it is treated as broken.
 *
 * Silence on its own is not a failure - a model thinking is supposed to be
 * quiet - so this number only means anything next to the server's heartbeat:
 * `HEARTBEAT_SECONDS` is 10, and two and a half of those missed is past any
 * scheduling jitter. What it covers is the one case nothing else does: a
 * connection that dies without saying so. A process killed, a machine powered
 * off, a proxy that holds the socket open - the reader never wakes up, and the
 * page sits at a cursor that will never move again.
 */
const IDLE_LIMIT_MS = 25_000

const IDLE_MESSAGE = '后端超过 25 秒没有发来任何内容。'

export async function* streamRun(
  path: string,
  init: RequestInit,
  signal: AbortSignal,
): AsyncGenerator<RunEvent> {
  // `withJsonBody` owns the headers. An earlier version built them here a second
  // time and spread `init.headers` (undefined at both call sites) over the top,
  // which silently dropped the content type - and a body that arrives without it
  // is not parsed as JSON at all, so every send answered 500.
  const response = await fetch(path, {
    ...withJsonBody(init, 'text/event-stream'),
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

  let idleTimer: ReturnType<typeof setTimeout> | undefined
  const startIdleTimer = () =>
    new Promise<never>((_, reject) => {
      idleTimer = setTimeout(() => reject(new Error(IDLE_MESSAGE)), IDLE_LIMIT_MS)
    })
  const stopIdleTimer = () => {
    clearTimeout(idleTimer)
    idleTimer = undefined
  }

  try {
    while (true) {
      // Racing the read against a timer, rather than asking fetch to time out,
      // is what makes the case above detectable: an abort only reaches a request
      // the platform still owns, and the connections that hang are exactly the
      // ones where it no longer does.
      const reading = reader.read()
      let chunk: ReadableStreamReadResult<Uint8Array>
      try {
        chunk = await Promise.race([reading, startIdleTimer()])
      } catch (error) {
        // Nothing is waiting for this read any more, and the cleanup below
        // settles it by cancelling the stream. Without a handler of its own,
        // however it settles would surface as an unhandled rejection.
        void reading.catch(() => undefined)
        throw error
      }
      // Any byte counts, a comment line included: the server sends one every
      // ten seconds precisely so that a run with nothing to say can still prove
      // it is there.
      stopIdleTimer()
      if (chunk.done) {
        return
      }
      // `stream: true` keeps a multi-byte character that straddles two network
      // chunks from being decoded as two broken halves. With Chinese text that
      // is not a corner case, it happens constantly.
      for (const frame of parser.push(decoder.decode(chunk.value, { stream: true }))) {
        yield decodeFrame(frame)
      }
    }
  } finally {
    stopIdleTimer()
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
