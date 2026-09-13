/**
 * What is on screen, as plain data.
 *
 * Everything about a toast that can be reasoned about without a browser lives
 * here: which ones exist, how many there may be, and what happens when one goes
 * away. The component on top of this owns only the timers, which is the part
 * that needs a DOM - and this repository's tests run without one.
 */

export type Tone = 'ok' | 'bad'

export interface ToastItem {
  id: number
  text: string
  tone: Tone
}

/** Three at once is already a pile. A fourth should not push the first one out
 *  before it has been read. */
export const MAX_TOASTS = 3

let counter = 0

/**
 * Ids only have to be unique among the toasts alive at the same moment, but this
 * one never goes backwards. That costs nothing and buys something: a timer that
 * fires late can only ever match a toast that has already gone, never one that
 * has taken its number.
 */
function nextId(): number {
  counter += 1
  return counter
}

export function push(list: ToastItem[], text: string, tone: Tone = 'ok'): ToastItem[] {
  // Newest at the end. The stack grows downward from the top-right corner, so
  // the newest one lands where the eye already is.
  const kept = [...list, { id: nextId(), text, tone }]
  return kept.length > MAX_TOASTS ? kept.slice(kept.length - MAX_TOASTS) : kept
}

export function drop(list: ToastItem[], id: number): ToastItem[] {
  return list.filter((item) => item.id !== id)
}
