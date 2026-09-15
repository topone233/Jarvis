/**
 * The quick-prompts tab's rules, kept out of the component and tested.
 *
 * The whole list is the save body - with a list this small, per-row saves
 * would only make ordering and deletion harder to reason about - and the one
 * thing worth checking before sending is that every row is filled in: the
 * composer renders a button per row, and an empty name would be a pill with
 * nothing on it. The API holds the same caps; the form checks first so the
 * answer is an inline sentence instead of a 422.
 */

import type { QuickPrompt } from './types'

/** The caps the API's schema enforces too, mirrored so the form cannot offer
 *  a row the store would reject. */
export const QUICK_PROMPT_MAX = 12
export const NAME_MAX = 50
export const PROMPT_MAX = 2_000

/** The first problem in the draft, or null when it would save. */
export function draftError(items: QuickPrompt[]): string | null {
  for (let index = 0; index < items.length; index += 1) {
    if (items[index].name.trim() === '') {
      return `第 ${index + 1} 条还没有名称。`
    }
    if (items[index].prompt.trim() === '') {
      return `第 ${index + 1} 条还没有提示词。`
    }
  }
  return null
}

/** The draft as a save body: trimmed, whole list at once. */
export function toPayload(items: QuickPrompt[]): { quick_prompts: QuickPrompt[] } {
  return {
    quick_prompts: items.map((item) => ({ name: item.name.trim(), prompt: item.prompt.trim() })),
  }
}

/**
 * The list with one row swapped past a neighbour.
 *
 * Built as a copy rather than in place, so a click that turns out to be a
 * no-op - the top row moved up, the bottom row moved down, an index past
 * either end - leaves the draft the state saw before it.
 */
export function moved(items: QuickPrompt[], index: number, step: -1 | 1): QuickPrompt[] {
  const target = index + step
  if (index < 0 || index >= items.length || target < 0 || target >= items.length) {
    return items
  }
  const next = items.slice()
  next[index] = items[target]
  next[target] = items[index]
  return next
}
