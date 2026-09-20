import { describe, expect, it } from 'vitest'

import {
  draftError,
  draftFrom,
  TOOL_LIMITS_DEFAULT_PATCH,
  toPayload,
  type ToolLimitsDraft,
} from './toolLimits'
import type { AppSettings } from './types'

const overview: AppSettings['tool_limits'] = {
  max_rounds: { value: 100, default: 100, is_default: true },
  repeat_window_seconds: { value: 30, default: 30, is_default: true },
  repeat_limit: { value: 10, default: 10, is_default: true },
}

function draft(over: Partial<ToolLimitsDraft> = {}): ToolLimitsDraft {
  return { max_rounds: '100', repeat_window_seconds: '30', repeat_limit: '10', ...over }
}

describe('what a save refuses', () => {
  it('passes filled-in numbers', () => {
    expect(draftError(draft())).toBeNull()
  })

  it('refuses a half-typed input', () => {
    expect(draftError(draft({ max_rounds: '' }))).toBe('还有一个值没有填。')
  })

  it('refuses a value outside its range', () => {
    expect(draftError(draft({ max_rounds: '1001' }))).toBe('工具轮次上限要在 1 到 1000 之间。')
    expect(draftError(draft({ repeat_window_seconds: '0' }))).toBe('判定窗口要在 1 到 3600 之间。')
    expect(draftError(draft({ repeat_limit: '0' }))).toBe('次数上限要在 1 到 1000 之间。')
  })
})

describe('what a save sends', () => {
  it('sends the parsed numbers', () => {
    expect(toPayload(draft({ max_rounds: ' 50 ' }))).toEqual({
      tool_max_rounds: 50,
      tool_repeat_window_seconds: 30,
      tool_repeat_limit: 10,
    })
  })

  it('sends three nulls for 恢复默认', () => {
    expect(TOOL_LIMITS_DEFAULT_PATCH).toEqual({
      tool_max_rounds: null,
      tool_repeat_window_seconds: null,
      tool_repeat_limit: null,
    })
  })
})

describe('drafting from the overview', () => {
  it('starts from the values in force', () => {
    expect(draftFrom(overview)).toEqual(draft())
  })
})
