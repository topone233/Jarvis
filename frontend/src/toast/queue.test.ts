import { describe, expect, it } from 'vitest'

import { drop, MAX_TOASTS, push, type ToastItem } from './queue'

const ids = (list: ToastItem[]) => list.map((item) => item.id)
const texts = (list: ToastItem[]) => list.map((item) => item.text)

function fill(count: number): ToastItem[] {
  let list: ToastItem[] = []
  for (let index = 0; index < count; index += 1) {
    list = push(list, `第 ${index} 条`)
  }
  return list
}

describe('toast queue', () => {
  it('gives every toast an id of its own', () => {
    const list = fill(MAX_TOASTS)
    expect(new Set(ids(list)).size).toBe(list.length)
  })

  it('keeps them in the order they arrived', () => {
    expect(texts(fill(MAX_TOASTS))).toEqual(['第 0 条', '第 1 条', '第 2 条'])
  })

  it('drops the oldest once there are more than three', () => {
    expect(texts(fill(MAX_TOASTS + 1))).toEqual(['第 1 条', '第 2 条', '第 3 条'])
  })

  it('does not reuse an id after the list has emptied', () => {
    const first = push([], '一件')
    const empty = drop(first, first[0].id)
    const second = push(empty, '另一件')
    // The whole point of a counter that never resets: a timer still pending from
    // the first toast cannot match the second one's id.
    expect(second[0].id).not.toBe(first[0].id)
  })

  it('removes only the one asked for', () => {
    const list = fill(MAX_TOASTS)
    expect(texts(drop(list, list[1].id))).toEqual(['第 0 条', '第 2 条'])
  })

  it('treats an id that is not there as nothing to do', () => {
    const list = fill(2)
    expect(drop(list, -1)).toEqual(list)
  })

  it('takes an explicit tone, and defaults to ok', () => {
    const list = push(push([], '出错了', 'bad'), '好了')
    expect(list.map((item) => item.tone)).toEqual(['bad', 'ok'])
  })
})
