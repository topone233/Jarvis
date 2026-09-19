import { describe, expect, it } from 'vitest'

import { activeIndex, outlineLabel, outlineLevel } from './outlineModel'

describe('outlineLevel', () => {
  it('makes user messages the top level and headings the indented one', () => {
    expect(outlineLevel('user')).toBe(1)
    expect(outlineLevel('h2')).toBe(2)
    expect(outlineLevel('h3')).toBe(2)
  })

  it('ignores every other stamp', () => {
    expect(outlineLevel('h4')).toBeNull()
    expect(outlineLevel('')).toBeNull()
  })
})

describe('outlineLabel', () => {
  it('collapses whitespace onto one line', () => {
    expect(outlineLabel('  第一段\n问题：\t怎么做？ ')).toBe('第一段 问题： 怎么做？')
  })

  it('cuts long text and says so', () => {
    const label = outlineLabel('很'.repeat(60))
    expect(label).toBe('很'.repeat(48) + '…')
  })

  it('leaves short text alone', () => {
    expect(outlineLabel('怎么部署？')).toBe('怎么部署？')
  })
})

describe('activeIndex', () => {
  it('answers -1 with nothing to navigate', () => {
    expect(activeIndex([], 96)).toBe(-1)
  })

  it('lands on the last section that has climbed past the reading line', () => {
    expect(activeIndex([26, 400, 900], 96)).toBe(0)
    expect(activeIndex([26, 90, 900], 96)).toBe(1)
    expect(activeIndex([26, 400, 890], 900)).toBe(2)
  })

  it('starts on the first section before any scrolling', () => {
    expect(activeIndex([26, 400], 96)).toBe(0)
  })
})
