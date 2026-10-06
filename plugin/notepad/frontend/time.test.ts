import { describe, expect, it } from 'vitest'

import { relativeTime } from './time'

// A fixed afternoon so the "today/yesterday" boundaries are far from midnight
// no matter which timezone the test machine sits in.
const NOW = new Date('2026-10-06T15:00:00').getTime()

function ago(ms: number): string {
  return new Date(NOW - ms).toISOString()
}

describe('relativeTime', () => {
  it('calls the fresh moment 刚刚', () => {
    expect(relativeTime(ago(30_000), NOW)).toBe('刚刚')
  })

  it('counts minutes up to the hour', () => {
    expect(relativeTime(ago(60_000), NOW)).toBe('1 分钟前')
    expect(relativeTime(ago(5 * 60_000), NOW)).toBe('5 分钟前')
    expect(relativeTime(ago(59 * 60_000), NOW)).toBe('59 分钟前')
  })

  it('shows the clock time for today, then 昨天', () => {
    expect(relativeTime(ago(2 * 3_600_000), NOW)).toBe('今天 13:00')
    expect(relativeTime(ago(20 * 3_600_000), NOW)).toBe('昨天 19:00')
  })

  it('falls back to a date, adding the year once it differs', () => {
    expect(relativeTime('2026-03-01T10:00:00', NOW)).toBe('3 月 1 日')
    expect(relativeTime('2024-08-09T10:00:00', NOW)).toBe('2024 年 8 月 9 日')
  })

  it('gives up quietly on an unreadable timestamp', () => {
    expect(relativeTime('not-a-date', NOW)).toBe('')
  })
})
