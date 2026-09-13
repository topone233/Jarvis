import { describe, expect, it } from 'vitest'

import { stageMillis, toSeconds, totalMillis } from './duration'
import type { AuditRow } from './reducer'

/** A stamp `seconds` after the same moment every `at()` shares. */
function at(seconds: number): string {
  return new Date(Date.UTC(2026, 8, 13, 6, 57, 14) + seconds * 1000).toISOString()
}

/** The format the backend actually writes: microseconds and a numeric offset. */
const MICRO = '2026-09-13T06:57:14.883699+00:00'

function row(overrides: Partial<AuditRow> = {}): AuditRow {
  return {
    stage: 'model_stream',
    state: 'completed',
    sequence: 1,
    payload: {},
    startedAt: null,
    endedAt: null,
    ...overrides,
  }
}

describe('stageMillis', () => {
  it('measures a finished stage between its two records', () => {
    expect(stageMillis(row({ startedAt: at(0), endedAt: at(3.14) }), 0)).toBe(3140)
  })

  it('measures a running stage against the clock', () => {
    const running = row({ state: 'running', startedAt: at(0), endedAt: null })
    expect(stageMillis(running, Date.parse(at(2.5)))).toBe(2500)
  })

  it('leaves a stage that never ran untimed', () => {
    // A skipped stage reports one record and no start, and a number beside it
    // would be a duration that never happened.
    const skipped = row({ state: 'skipped', startedAt: null, endedAt: at(1) })
    expect(stageMillis(skipped, 0)).toBeNull()
  })

  it('never counts backwards', () => {
    // The start is the backend's clock and `now` is the browser's. A few
    // milliseconds of disagreement must not surface as a negative duration.
    expect(stageMillis(row({ startedAt: at(1), endedAt: at(0) }), 0)).toBe(0)
  })

  it('reads the backend’s own timestamp format', () => {
    // A parse that quietly failed would show no timings at all rather than
    // wrong ones, which is exactly the kind of silence worth pinning down.
    const ended = '2026-09-13T06:57:16.383699+00:00'
    expect(stageMillis(row({ startedAt: MICRO, endedAt: ended }), 0)).toBe(1500)
  })

  it('treats an unreadable stamp as no time at all', () => {
    expect(stageMillis(row({ startedAt: 'not a date', endedAt: at(1) }), 0)).toBeNull()
  })
})

describe('totalMillis', () => {
  it('runs from the first stage starting to the last one ending', () => {
    const audits = [
      row({ stage: 'context_compaction', startedAt: at(0), endedAt: at(0.5) }),
      row({ stage: 'model_stream', startedAt: at(0.5), endedAt: at(4.31) }),
    ]
    expect(totalMillis(audits, 0)).toBe(4310)
  })

  it('keeps counting while the last stage is still running', () => {
    const audits = [
      row({ stage: 'context_compaction', startedAt: at(0), endedAt: at(0.5) }),
      row({ stage: 'model_stream', state: 'running', startedAt: at(0.5), endedAt: null }),
    ]
    expect(totalMillis(audits, Date.parse(at(3)))).toBe(3000)
  })

  it('measures across the stages that were timed, ignoring the rest', () => {
    const audits = [
      row({ stage: 'context_compaction', state: 'skipped', startedAt: null, endedAt: at(0.5) }),
      row({ stage: 'model_stream', startedAt: at(1), endedAt: at(2) }),
    ]
    // The skipped stage has an end and no start, so it cannot drag the total
    // back to the beginning of the conversation - the first real stage sets it.
    expect(totalMillis(audits, 0)).toBe(1000)
  })

  it('has no total before anything has been timed', () => {
    expect(totalMillis([], 0)).toBeNull()
    expect(totalMillis([row()], 0)).toBeNull()
  })
})

describe('toSeconds', () => {
  it('shows two places', () => {
    expect(toSeconds(0)).toBe('0.00')
    expect(toSeconds(1000)).toBe('1.00')
    expect(toSeconds(3140)).toBe('3.14')
  })

  it('rounds to the nearer hundredth', () => {
    expect(toSeconds(3141)).toBe('3.14')
    expect(toSeconds(3149)).toBe('3.15')
  })
})
