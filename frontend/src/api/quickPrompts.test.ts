import { describe, expect, it } from 'vitest'

import { draftError, moved, toPayload } from './quickPrompts'
import type { QuickPrompt } from './types'

function item(name: string, prompt = '记一下：'): QuickPrompt {
  return { name, prompt }
}

describe('what a save refuses', () => {
  it('passes a filled-in list', () => {
    expect(draftError([item('记一下'), item('记待办', '记个待办：')])).toBeNull()
  })

  it('names the first row with no name', () => {
    expect(draftError([item(''), item('记待办')])).toBe('第 1 条还没有名称。')
  })

  it('names the row whose prompt is missing', () => {
    expect(draftError([item('记一下', '')])).toBe('第 1 条还没有提示词。')
  })

  it('counts whitespace as empty', () => {
    expect(draftError([item('  ')])).toBe('第 1 条还没有名称。')
  })
})

describe('what a save sends', () => {
  it('sends the trimmed list', () => {
    expect(toPayload([item(' 记一下 '), item('记待办', ' 记个待办： ')])).toEqual({
      quick_prompts: [
        { name: '记一下', prompt: '记一下：' },
        { name: '记待办', prompt: '记个待办：' },
      ],
    })
  })

  it('sends an emptied list as an empty list, which is a real choice', () => {
    expect(toPayload([])).toEqual({ quick_prompts: [] })
  })
})

describe('reordering', () => {
  const list = [item('一'), item('二'), item('三')]

  it('swaps a row with the one above it', () => {
    expect(moved(list, 1, -1).map((row) => row.name)).toEqual(['二', '一', '三'])
  })

  it('swaps a row with the one below it', () => {
    expect(moved(list, 1, 1).map((row) => row.name)).toEqual(['一', '三', '二'])
  })

  it('leaves the ends alone', () => {
    expect(moved(list, 0, -1)).toEqual(list)
    expect(moved(list, 2, 1)).toEqual(list)
  })

  it('does not touch the list it was given', () => {
    moved(list, 0, 1)
    expect(list.map((row) => row.name)).toEqual(['一', '二', '三'])
  })
})
