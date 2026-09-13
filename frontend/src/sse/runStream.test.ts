import { afterEach, describe, expect, it, vi } from 'vitest'

import type { RunEvent } from '../runs/events'
import { streamRun } from './runStream'

/** A response body that behaves like the real one: a stream read through
 *  `getReader()`, not a string handed over whole. */
function sseResponse(text: string): Response {
  return new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(text))
        controller.close()
      },
    }),
    { status: 200 },
  )
}

/** A body that stays open and never sends a byte - what a dead connection looks
 *  like to the reader, and what the watchdog exists to catch. */
function silentResponse(): Response {
  return new Response(new ReadableStream({ start() {} }), { status: 200 })
}

/** A body that sends each frame on its own timer, then closes on the last one. */
function tickingResponse(frames: string[], everyMs: number): Response {
  return new Response(
    new ReadableStream({
      start(controller) {
        const encoder = new TextEncoder()
        frames.forEach((frame, index) => {
          setTimeout(
            () => {
              controller.enqueue(encoder.encode(frame))
              if (index === frames.length - 1) {
                controller.close()
              }
            },
            everyMs * (index + 1),
          )
        })
      },
    }),
    { status: 200 },
  )
}

/** Runs the generator to the end and returns everything it yielded. */
async function drain(events: AsyncGenerator<RunEvent>): Promise<RunEvent[]> {
  const seen: RunEvent[] = []
  for await (const event of events) {
    seen.push(event)
  }
  return seen
}

describe('streamRun', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('sends the content type alongside the stream accept header', async () => {
    // The regression this guards: the headers were assembled here a second time
    // and the content type went missing, so the backend was handed a body it
    // would not parse and answered 500 to every send.
    const sent: RequestInit[] = []
    vi.stubGlobal('fetch', async (_path: string, init: RequestInit) => {
      sent.push(init)
      return sseResponse(
        'event: run.started\ndata: {"run_id":"r1","assistant_message_id":"m1"}\n\n',
      )
    })

    await drain(
      streamRun(
        '/api/conversations/c1/runs',
        { method: 'POST', body: '{"content":"你好"}' },
        new AbortController().signal,
      ),
    )

    expect(sent).toHaveLength(1)
    expect(sent[0].headers).toMatchObject({
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    })
  })

  it('leaves the content type off a request that has no body', async () => {
    // Reattaching to a run is a bare GET. Sending a content type there would be
    // describing a body that does not exist.
    const sent: RequestInit[] = []
    vi.stubGlobal('fetch', async (_path: string, init: RequestInit) => {
      sent.push(init)
      return sseResponse('event: run.failed\ndata: {"error":"没了"}\n\n')
    })

    const events = await drain(streamRun('/api/runs/r1/stream', {}, new AbortController().signal))

    expect(sent[0].headers).toEqual({ Accept: 'text/event-stream' })
    expect(events).toEqual([{ type: 'run.failed', error: '没了' }])
  })

  it('passes the abort signal through to fetch', async () => {
    // Cancelling is the signal's job; if it stops being forwarded, the stop
    // button silently stops working and nobody notices until a run hangs.
    const sent: RequestInit[] = []
    vi.stubGlobal('fetch', async (_path: string, init: RequestInit) => {
      sent.push(init)
      return sseResponse('')
    })
    const controller = new AbortController()

    await drain(streamRun('/api/runs/r1/stream', {}, controller.signal))

    expect(sent[0].signal).toBe(controller.signal)
  })

  it('gives up on a connection that says nothing at all', async () => {
    // The case no abort can reach: the process behind the stream is gone and
    // nothing told the socket. Without this the reader waits forever, and the
    // page shows a cursor that will never move again.
    vi.useFakeTimers()
    vi.stubGlobal('fetch', async () => silentResponse())
    const events = drain(streamRun('/api/runs/r1/stream', {}, new AbortController().signal))
    const failure = expect(events).rejects.toThrow('25 秒')

    await vi.advanceTimersByTimeAsync(1_000)
    await vi.advanceTimersByTimeAsync(30_000)

    await failure
  })

  it('is not fooled while the server keeps saying something', async () => {
    // The dangerous half of the same mechanism: a run with nothing to report
    // stays open for as long as the model thinks, and its heartbeat comments are
    // the only evidence it is alive. Treating them as silence would kill every
    // long answer.
    vi.useFakeTimers()
    vi.stubGlobal('fetch', async () =>
      tickingResponse(
        [
          ': keep-alive\n\n',
          ': keep-alive\n\n',
          'event: message.completed\ndata: {"message_id":"m1","content":"好啦"}\n\n',
        ],
        10_000,
      ),
    )

    const events = drain(streamRun('/api/runs/r1/stream', {}, new AbortController().signal))
    await vi.advanceTimersByTimeAsync(45_000)

    expect(await events).toMatchObject([{ type: 'message.completed' }])
  })
})
