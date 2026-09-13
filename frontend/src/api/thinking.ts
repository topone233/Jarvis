/**
 * The composer's thinking dial: one control, four stops.
 *
 * "off" is the profile's own business, and the only stop that is - asking a
 * provider not to think has no single spelling, so the backend sends whatever
 * that profile calls thinking-off. The other three are one name this app sets
 * itself: a strength sends `reasoning_effort` and nothing else, which is why it
 * needs nothing configured and why the slider is drawn for every profile.
 *
 * The list lives here rather than in the component because three things have to
 * agree about it - the stops the slider draws, what a choice carries, and what
 * the backend's `ThinkingLevel` accepts.
 */

export const THINKING_LEVELS = ['off', 'low', 'high', 'max'] as const

export type ThinkingLevel = (typeof THINKING_LEVELS)[number]

/**
 * What each stop is called on screen.
 *
 * The three strengths are the provider's own words and go on the wire as
 * written, so they are shown as written too - translating them would put a
 * second vocabulary between the user and the field they are setting.
 */
export const THINKING_LABELS: Record<ThinkingLevel, string> = {
  off: '关',
  low: 'low',
  high: 'high',
  max: 'max',
}

/** Where a level sits on the slider. */
export function levelIndex(level: ThinkingLevel): number {
  return THINKING_LEVELS.indexOf(level)
}

/**
 * The level at a slider position.
 *
 * A range input answers with a number and nothing else, so the position has to
 * be turned back into one of the four. `min`, `max` and `step` on the input are
 * what keep the number inside the list - there is no second bound here, because
 * a second bound would be the one that is wrong.
 */
export function levelAt(index: number): ThinkingLevel {
  return THINKING_LEVELS[index]
}
