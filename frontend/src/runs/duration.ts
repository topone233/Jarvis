/**
 * How long each step of an answer took.
 *
 * Nothing here is simulated and nothing is invented. The backend stamps every
 * audit record with `created_at`, and a stage is two of those - it began, it
 * ended - so a duration is one minus the other. The same two numbers arrive
 * whether the answer is still being written or was read back from disk long
 * afterwards, which is why a reloaded page shows the same seconds the live one
 * showed.
 *
 * Everything is in milliseconds; `formatDuration` is the one place that
 * decides how the screen says a length of time.
 */

import type { AuditRow } from './reducer'

/** How long one stage has taken so far. A running stage is measured to `now`. */
export function stageMillis(row: AuditRow, now: number): number | null {
  const from = parsed(row.startedAt)
  if (from === null) {
    return null
  }
  const to = row.state === 'running' ? now : parsed(row.endedAt)
  if (to === null) {
    return null
  }
  // Never negative: the stamps come from the backend's clock and `now` from the
  // browser's, and a few milliseconds of disagreement must not show up on
  // screen as a stage that finished before it started.
  return Math.max(to - from, 0)
}

/** How long the whole answer has taken - first stage's start to last stage's end. */
export function totalMillis(audits: AuditRow[], now: number): number | null {
  const starts = audits.map((row) => parsed(row.startedAt)).filter(isNumber)
  const ends = audits
    .map((row) => (row.state === 'running' ? now : parsed(row.endedAt)))
    .filter(isNumber)
  if (starts.length === 0 || ends.length === 0) {
    return null
  }
  return Math.max(Math.max(...ends) - Math.min(...starts), 0)
}

/**
 * Milliseconds as the compact duration the strip shows, whole seconds up:
 * `0.4秒` below a second, `3秒` up to the first minute, `1分5秒` past it -
 * the step timers redraw many times a second, and a wall of hundredths makes
 * every row twitch. The caller renders the digits in the mono stack; the
 * units stay in the running text.
 */
export function formatDuration(millis: number): string {
  const clamped = Math.max(0, millis)
  const total = Math.floor(clamped / 1000)
  if (total >= 60) {
    return `${Math.floor(total / 60)}分${total % 60}秒`
  }
  if (clamped < 10_000) {
    return `${(clamped / 1000).toFixed(1)}秒`
  }
  return `${total}秒`
}

function parsed(stamp: string | null): number | null {
  if (stamp === null || stamp === '') {
    return null
  }
  const value = Date.parse(stamp)
  return Number.isNaN(value) ? null : value
}

function isNumber(value: number | null): value is number {
  return value !== null
}
