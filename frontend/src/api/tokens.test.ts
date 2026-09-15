import { describe, expect, it } from 'vitest'

import { draftTokens, estimateTokens, IMAGE_TOKEN_ESTIMATE, ringState } from './tokens'

describe('the estimator', () => {
  it('mirrors the backend: CJK counts heavier than the rest', () => {
    // ceil(10 * 1.2) for the CJK half, the backend's own worked example.
    expect(estimateTokens('一二三四五六七八九十')).toBe(12)
    expect(estimateTokens('abcdefgh')).toBe(2)
    expect(estimateTokens('四个人 abcd')).toBe(Math.ceil(3 * 1.2 + 5 / 4))
  })

  it('never says zero', () => {
    expect(estimateTokens('')).toBe(1)
  })
})

describe('the draft total', () => {
  it('adds one constant per image', () => {
    expect(draftTokens('看看这个', 2)).toBe(estimateTokens('看看这个') + 2 * IMAGE_TOKEN_ESTIMATE)
  })

  it('does not charge for a text box that was never typed into', () => {
    expect(draftTokens('   ', 0)).toBe(0)
  })
})

describe('the ring', () => {
  const budget = 10_000

  it('is ordinary while the draft is small next to the budget', () => {
    expect(ringState(2_000, budget)).toBe('ok')
  })

  it('warns from eighty percent', () => {
    expect(ringState(7_999, budget)).toBe('ok')
    expect(ringState(8_000, budget)).toBe('warn')
  })

  it('turns over exactly at the budget', () => {
    expect(ringState(9_999, budget)).toBe('warn')
    expect(ringState(10_000, budget)).toBe('over')
  })
})
