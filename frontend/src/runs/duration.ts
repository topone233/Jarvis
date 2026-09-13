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
 * Everything is in milliseconds; `toSeconds` is the one place that decides how
 * many places the screen shows.
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

/** Milliseconds as seconds to two places, which is the precision shown. */
export function toSeconds(millis: number): string {
  return (millis / 1000).toFixed(2)
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
