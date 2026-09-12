/**
 * Turns a byte stream into Server-Sent Event frames.
 *
 * Pure text handling, per the WHATWG event-stream rules: no knowledge of Jarvis
 * and no JSON. Everything here exists because a network chunk boundary has
 * nothing to do with an event boundary - a frame can be split mid-line, mid-CRLF,
 * or several frames can arrive at once.
 */

export interface SseFrame {
  /** The `event:` field, or `'message'` when the server did not name one. */
  event: string
  /** The `data:` field(s), joined with newlines. */
  data: string
}

export interface SseParser {
  /** Feed decoded text; returns whatever frames are now complete. */
  push(chunk: string): SseFrame[]
}

const BYTE_ORDER_MARK = '﻿'

export function createSseParser(): SseParser {
  let buffered = ''
  let eventName = ''
  let dataLines: string[] = []
  let atStreamStart = true

  function takeFrame(out: SseFrame[]): void {
    // An `event:` with no `data:` is not dispatched - the spec treats data as
    // what makes a frame real, and a bare name carries nothing to render.
    if (dataLines.length > 0) {
      out.push({ event: eventName || 'message', data: dataLines.join('\n') })
    }
    eventName = ''
    dataLines = []
  }

  function takeLine(line: string, out: SseFrame[]): void {
    if (line === '') {
      takeFrame(out)
      return
    }
    if (line.startsWith(':')) {
      // A comment. Servers send these to keep idle connections alive.
      return
    }
    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    let value = colon === -1 ? '' : line.slice(colon + 1)
    // Exactly one leading space is part of the framing, not the value.
    if (value.startsWith(' ')) {
      value = value.slice(1)
    }
    if (field === 'event') {
      eventName = value
    } else if (field === 'data') {
      dataLines.push(value)
    }
    // `id:`, `retry:` and anything else are accepted and ignored: this client
    // tracks its own position and never asks the server to resume.
  }

  return {
    push(chunk: string): SseFrame[] {
      buffered += chunk
      if (atStreamStart) {
        atStreamStart = false
        if (buffered.startsWith(BYTE_ORDER_MARK)) {
          buffered = buffered.slice(BYTE_ORDER_MARK.length)
        }
      }

      const out: SseFrame[] = []
      let cursor = 0
      while (true) {
        const index = findLineBreak(buffered, cursor)
        if (index === -1) {
          break
        }
        // A CR at the very end may be the first half of a CRLF whose second
        // half has not arrived yet. Consuming it now would invent an empty
        // line, which would dispatch a frame early.
        if (buffered[index] === '\r' && index === buffered.length - 1) {
          break
        }
        const width = buffered[index] === '\r' && buffered[index + 1] === '\n' ? 2 : 1
        takeLine(buffered.slice(cursor, index), out)
        cursor = index + width
      }
      buffered = buffered.slice(cursor)
      return out
    },
  }
}

function findLineBreak(text: string, from: number): number {
  for (let index = from; index < text.length; index += 1) {
    const character = text[index]
    if (character === '\n' || character === '\r') {
      return index
    }
  }
  return -1
}
