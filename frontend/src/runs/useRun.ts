/**
 * Owns one assistant turn: starting it, watching it, losing it, and picking it
 * back up.
 *
 * The rule that shapes everything here: retrying must be idempotent. Asking the
 * server about a known run id and reattaching is safe; re-POSTing a message is
 * not, because that would create a second run and send the same text twice. So a
 * failure before the run id is known is final, and a failure after it is just a
 * reconnect.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { cancelRun, getRun, listRunEvents } from '../api/endpoints'
import { isTerminalEvent, streamRun } from '../sse/runStream'
import { createTurn, reduce, type TurnAction, type TurnSeed, type TurnState } from './reducer'

/**
 * How long to wait before each reattach attempt. The tail is long enough that a
 * backend restarted within about half a minute is picked up on its own; past
 * that the UI offers a button rather than hammering a service that is gone.
 */
const RETRY_DELAYS = [500, 1000, 2000, 4000, 8000, 15000]

let localCounter = 0

export interface RunController {
  turn: TurnState | null
  send(content: string): void
  regenerate(messageId: string): void
  attach(runId: string, seed?: TurnSeed): void
  cancel(): void
  reconnect(): void
  /** True once retries are exhausted and only a manual reconnect is left. */
  gaveUp: boolean
}

export function useRun(conversationId: string | null, onChanged: () => void): RunController {
  const [turn, setTurn] = useState<TurnState | null>(null)
  const [gaveUp, setGaveUp] = useState(false)
  const turnRef = useRef<TurnState | null>(null)
  const frameRef = useRef<number | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const changedRef = useRef(onChanged)

  useEffect(() => {
    changedRef.current = onChanged
  }, [onChanged])

  // Text churn is flushed at most once per frame. A fast model sends deltas far
  // more often than the screen refreshes, and re-rendering the whole Markdown
  // answer per token is the one thing that makes streaming feel slow.
  const commit = useCallback((action: TurnAction) => {
    if (turnRef.current === null) {
      return
    }
    turnRef.current = reduce(turnRef.current, action)
    if (frameRef.current === null) {
      frameRef.current = requestAnimationFrame(() => {
        frameRef.current = null
        setTurn(turnRef.current)
      })
    }
  }, [])

  // Structural changes - a new turn, a turn ending - go out immediately; only
  // the contents of the text are worth delaying.
  const replaceTurn = useCallback((next: TurnState | null) => {
    turnRef.current = next
    if (frameRef.current !== null) {
      cancelAnimationFrame(frameRef.current)
      frameRef.current = null
    }
    setTurn(next)
    setGaveUp(false)
  }, [])

  const drive = useCallback(
    async (firstPath: string, firstInit: RequestInit, knownRunId: string | null) => {
      abortRef.current?.abort()
      const controller = new AbortController()
      abortRef.current = controller
      const { signal } = controller

      let runId = knownRunId
      let attempt = 0
      let terminal = false

      while (!signal.aborted) {
        try {
          let path = firstPath
          let init = firstInit
          if (runId !== null) {
            // The run record is the authority on what is happening, and the
            // only thing that can say *why* a run failed.
            const record = await getRun(runId)
            if (signal.aborted) return
            commit({ type: 'snapshot', status: record.status })
            path = `/api/runs/${runId}/stream`
            init = { method: 'GET' }
          }
          commit({ type: 'attached' })

          for await (const event of streamRun(path, init, signal)) {
            if (event.type === 'run.started' && event.runId !== '') {
              runId = event.runId
            }
            commit({ type: 'event', event })
            if (isTerminalEvent(event)) {
              terminal = true
            }
          }

          if (terminal) {
            // Audits trail the terminal event - the memory write is reported
            // after the answer is on screen - so the stream is read to its end
            // before this returns.
            return
          }
          // Closed with no outcome. Ask about the run again instead of leaving
          // a cursor blinking at something that may already be over.
          throw new Error('流在结束前就关闭了。')
        } catch (error) {
          if (signal.aborted) return

          if (runId === null) {
            // Nothing was created that we can point at, so there is nothing
            // safe to retry. Report it and let the caller re-read the history.
            commit({
              type: 'event',
              event: { type: 'run.failed', error: describe(error) },
            })
            changedRef.current()
            return
          }

          attempt += 1
          if (attempt > RETRY_DELAYS.length) {
            commit({ type: 'detached' })
            setGaveUp(true)
            return
          }
          commit({ type: 'detached' })
          await sleep(RETRY_DELAYS[attempt - 1], signal)
        }
      }
    },
    [commit],
  )

  const send = useCallback(
    (content: string) => {
      if (conversationId === null) return
      localCounter += 1
      replaceTurn(createTurn(`local-${localCounter}`))
      void drive(
        `/api/conversations/${conversationId}/runs`,
        { method: 'POST', body: JSON.stringify({ content }) },
        null,
      )
    },
    [conversationId, drive, replaceTurn],
  )

  const regenerate = useCallback(
    (messageId: string) => {
      localCounter += 1
      // Regenerating reuses the same assistant message on the server, so the
      // existing bubble is what streams the new answer.
      replaceTurn(createTurn(`local-${localCounter}`, { assistantMessageId: messageId }))
      void drive(`/api/messages/${messageId}/regenerate`, { method: 'POST', body: '{}' }, null)
    },
    [drive, replaceTurn],
  )

  const attach = useCallback(
    (runId: string, seed?: TurnSeed) => {
      localCounter += 1
      replaceTurn(createTurn(`local-${localCounter}`, { ...seed, runId }))
      void backfillAudits(runId, commit)
      void drive(`/api/runs/${runId}/stream`, { method: 'GET' }, runId)
    },
    [commit, drive, replaceTurn],
  )

  const cancel = useCallback(() => {
    const runId = turnRef.current?.runId
    if (!runId) return
    // The stream is left alone on purpose: the server answers a cancel with a
    // `run.cancelled` event, and abandoning the connection would throw away the
    // partial text it carries.
    void cancelRun(runId).catch(() => undefined)
  }, [])

  const reconnect = useCallback(() => {
    const runId = turnRef.current?.runId
    if (!runId) return
    replaceTurn({ ...turnRef.current!, detached: false })
    void backfillAudits(runId, commit)
    void drive(`/api/runs/${runId}/stream`, { method: 'GET' }, runId)
  }, [commit, drive, replaceTurn])

  useEffect(() => {
    return () => {
      abortRef.current?.abort()
      if (frameRef.current !== null) {
        cancelAnimationFrame(frameRef.current)
      }
    }
  }, [])

  return { turn, send, regenerate, attach, cancel, reconnect, gaveUp }
}

function describe(error: unknown): string {
  if (error instanceof Error && error.message !== '') {
    return error.message
  }
  return '这一次回复没能发出去。'
}

function sleep(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, milliseconds)
    signal.addEventListener(
      'abort',
      () => {
        clearTimeout(timer)
        resolve()
      },
      { once: true },
    )
  })
}

/**
 * Fills in rounds this client never saw, so a progress strip read after a
 * reload is not missing everything that happened before it arrived.
 */
async function backfillAudits(runId: string, commit: (action: TurnAction) => void): Promise<void> {
  try {
    const records = await listRunEvents(runId)
    commit({ type: 'audits', records })
  } catch {
    // The live events still arrive; a missing history only shortens the strip.
  }
}

export { RETRY_DELAYS }
