import { describe, expect, it } from 'vitest'

import { THINKING_LEVELS, levelAt, levelIndex } from './thinking'

describe('the thinking dial', () => {
  // The backend validates this against a Python Literal with the same four
  // values, and nothing links the two languages. So this is the only place the
  // contract is written down on this side: reorder the slider and the wire
  // changes with it, silently.
  it('has the four stops the backend accepts, in the order the slider draws them', () => {
    expect(THINKING_LEVELS).toEqual(['off', 'low', 'high', 'max'])
  })

  // The slider's value is a number and everything else is a level, so the two
  // have to be each other's inverse - that is what lets the thumb's position be
  // the setting, with no second piece of state to drift from it.
  it('round-trips every stop through its slider position', () => {
    for (const level of THINKING_LEVELS) {
      expect(levelAt(levelIndex(level))).toBe(level)
    }
    expect(levelIndex('off')).toBe(0)
    expect(levelIndex('max')).toBe(THINKING_LEVELS.length - 1)
  })
})
