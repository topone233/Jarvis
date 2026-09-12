import { describe, expect, it } from 'vitest'

import { createSseParser, type SseFrame } from './parse'

const FRAME = 'event: message.delta\ndata: {"delta":"你好"}\n\n'

// Spelled out rather than embedded, so the character stays visible in source.
const BOM = String.fromCharCode(0xfeff)

function feed(chunks: string[]): SseFrame[] {
  const parser = createSseParser()
  return chunks.flatMap((chunk) => parser.push(chunk))
}

describe('createSseParser', () => {
  it('reads a whole frame', () => {
    expect(feed([FRAME])).toEqual([{ event: 'message.delta', data: '{"delta":"你好"}' }])
  })

  it('reads a frame split at every possible point', () => {
    for (let cut = 0; cut <= FRAME.length; cut += 1) {
      const frames = feed([FRAME.slice(0, cut), FRAME.slice(cut)])
      expect(frames, `split at ${cut}`).toEqual([
        { event: 'message.delta', data: '{"delta":"你好"}' },
      ])
    }
  })

  it('reads a frame split into three pieces', () => {
    const frames = feed([FRAME.slice(0, 5), FRAME.slice(5, 20), FRAME.slice(20)])
    expect(frames).toHaveLength(1)
  })

  it('reads several frames from one chunk', () => {
    const frames = feed([FRAME + 'event: audit\ndata: {"stage":"model_stream"}\n\n'])
    expect(frames.map((frame) => frame.event)).toEqual(['message.delta', 'audit'])
  })

  it('holds back a frame until its blank line arrives', () => {
    expect(feed(['event: message.delta\ndata: {"delta":"a"}\n'])).toEqual([])
    expect(feed(['data: {"delta":"a"}'])).toEqual([])
  })

  it('accepts CRLF line endings', () => {
    const frames = feed(['event: audit\r\ndata: {"stage":"x"}\r\n\r\n'])
    expect(frames).toEqual([{ event: 'audit', data: '{"stage":"x"}' }])
  })

  it('waits when a chunk ends between CR and LF', () => {
    const parser = createSseParser()
    expect(parser.push('event: audit\r')).toEqual([])
    expect(parser.push('\ndata: {"stage":"x"}\r\n\r\n')).toEqual([
      { event: 'audit', data: '{"stage":"x"}' },
    ])
  })

  it('joins multi-line data with newlines', () => {
    expect(feed(['event: x\ndata: one\ndata: two\n\n'])).toEqual([{ event: 'x', data: 'one\ntwo' }])
  })

  it('ignores comment lines', () => {
    expect(feed([': keep-alive\nevent: x\ndata: {}\n\n'])).toEqual([{ event: 'x', data: '{}' }])
  })

  it('strips exactly one leading space from a value', () => {
    expect(feed(['event: x\ndata:  two\n\n'])).toEqual([{ event: 'x', data: ' two' }])
  })

  it('accepts a field with no space after the colon', () => {
    expect(feed(['event:x\ndata:{}\n\n'])).toEqual([{ event: 'x', data: '{}' }])
  })

  it('names an unnamed event "message"', () => {
    expect(feed(['data: {}\n\n'])).toEqual([{ event: 'message', data: '{}' }])
  })

  it('drops a frame that has an event name but no data', () => {
    expect(feed(['event: x\n\n'])).toEqual([])
  })

  it('ignores id and retry fields', () => {
    expect(feed(['id: 7\nretry: 500\nevent: x\ndata: {}\n\n'])).toEqual([
      { event: 'x', data: '{}' },
    ])
  })

  it('discards an unterminated frame at the end of the stream', () => {
    // The spec says pending data is thrown away once the stream ends, and the
    // server always terminates a frame, so a trailing fragment means the
    // connection was cut - reconnecting is the answer, not guessing at JSON.
    const parser = createSseParser()
    expect(parser.push('event: x\ndata: {"partial":')).toEqual([])
  })

  it('strips a byte order mark at the start of the stream', () => {
    expect(feed([BOM + 'event: x\ndata: {}\n\n'])).toEqual([{ event: 'x', data: '{}' }])
  })
})
