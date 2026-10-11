/**
 * The composer's thinking dial: one control, six stops.
 *
 * "off" is the profile's own business, and the only stop that is - asking a
 * provider not to think has no single spelling, so the backend sends whatever
 * that profile calls thinking-off. The other five are one name this app sets
 * itself: a strength sends `reasoning_effort` and nothing else, which is why
 * it needs nothing configured and why the segments are drawn for every
 * profile. The ladder is the providers' own vocabulary, extended as endpoints
 * learn new spellings - one that refuses a strength fails the run with the
 * provider's own words, which is the honest outcome.
 *
 * The list lives here rather than in the component because three things have
 * to agree about it - the stops the segments draw, what a choice carries, and
 * what the backend's `ThinkingLevel` accepts.
 */

export const THINKING_LEVELS = ['off', 'low', 'medium', 'high', 'xhigh', 'max'] as const

export type ThinkingLevel = (typeof THINKING_LEVELS)[number]

/**
 * What each stop is called on screen.
 *
 * The strengths are the provider's own words and go on the wire as written,
 * so they are shown as written too - translating them would put a second
 * vocabulary between the user and the field they are setting.
 */
export const THINKING_LABELS: Record<ThinkingLevel, string> = {
  off: '关',
  low: 'low',
  medium: 'medium',
  high: 'high',
  xhigh: 'xhigh',
  max: 'max',
}

/** Where a level sits among the stops. */
export function levelIndex(level: ThinkingLevel): number {
  return THINKING_LEVELS.indexOf(level)
}

/**
 * The level at a position in the list.
 *
 * Kept beside `levelIndex` because the two are one mapping read in opposite
 * directions; the segmented control clicks levels directly and does not need
 * it, but anything that stores a position does.
 */
export function levelAt(index: number): ThinkingLevel {
  return THINKING_LEVELS[index]
}

/** Whether a value read back from storage is still one of the stops. */
export function isThinkingLevel(value: unknown): value is ThinkingLevel {
  return typeof value === 'string' && (THINKING_LEVELS as readonly string[]).includes(value)
}
