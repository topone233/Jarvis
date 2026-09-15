import { describe, expect, it } from 'vitest'

import { MAX_IMAGE_SIDE, needsReencode, scaledSize } from './images'

describe('scaledSize', () => {
  it('leaves a small picture alone', () => {
    expect(scaledSize(800, 600)).toEqual({ width: 800, height: 600 })
  })

  it('caps the long edge and keeps the proportion', () => {
    expect(scaledSize(3_136, 784)).toEqual({ width: MAX_IMAGE_SIDE, height: 392 })
    expect(scaledSize(784, 3_136)).toEqual({ width: 392, height: MAX_IMAGE_SIDE })
  })

  it('never upscales', () => {
    expect(scaledSize(100, 50, 2_000)).toEqual({ width: 100, height: 50 })
  })
})

describe('needsReencode', () => {
  it('declines a small-enough, light-enough image', () => {
    expect(needsReencode(1_000, 800, 100_000)).toBe(false)
  })

  it('accepts a picture past its long edge even when it is light', () => {
    expect(needsReencode(4_000, 2_000, 50_000)).toBe(true)
  })

  it('accepts a heavy picture even when it fits', () => {
    expect(needsReencode(1_000, 800, 400_000)).toBe(true)
  })
})
