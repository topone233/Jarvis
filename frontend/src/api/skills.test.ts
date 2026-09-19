import { describe, expect, it } from 'vitest'

import { importPayload } from './skills'

describe('building an import', () => {
  it('trims the path and sends it as the body', () => {
    expect(importPayload(' D:\\skills\\demo ')).toEqual({ path: 'D:\\skills\\demo' })
  })

  it('refuses a blank box before anything is sent', () => {
    expect(importPayload('   ')).toContain('路径')
  })
})
