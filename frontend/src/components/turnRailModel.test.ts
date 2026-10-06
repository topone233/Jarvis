import { describe, expect, it } from 'vitest'

import { activeTurnIndex, collapseLine } from './turnRailModel'

describe('collapseLine', () => {
  it('collapses whitespace onto one line', () => {
    expect(collapseLine('  第一段\n问题：\t怎么做？ ', 48)).toBe('第一段 问题： 怎么做？')
  })

  it('cuts long text and says so', () => {
    const label = collapseLine('很'.repeat(60), 48)
    expect(label).toBe('很'.repeat(48) + '…')
  })

  it('leaves short text alone', () => {
    expect(collapseLine('怎么部署？', 48)).toBe('怎么部署？')
  })
})

describe('activeTurnIndex', () => {
  it('answers -1 with nothing to navigate', () => {
    expect(activeTurnIndex([], 96)).toBe(-1)
  })

  it('lands on the last turn that has climbed past the reading line', () => {
    expect(activeTurnIndex([26, 400, 900], 96)).toBe(0)
    expect(activeTurnIndex([26, 90, 900], 96)).toBe(1)
    expect(activeTurnIndex([26, 400, 890], 900)).toBe(2)
  })

  it('starts on the first turn before any scrolling', () => {
    expect(activeTurnIndex([26, 400], 96)).toBe(0)
  })
})
