import { describe, expect, it } from 'vitest'

import {
  bashDraftFrom,
  draftError,
  draftFrom,
  TOOL_LIMITS_DEFAULT_PATCH,
  toPayload,
  workingDirError,
  type BashDraft,
  type ToolLimitsDraft,
} from './toolLimits'
import type { AppSettings } from './types'

const overview: AppSettings = {
  prompts: {
    system_prompt: { text: '', default_text: '', is_default: true },
    compaction_prompt: { text: '', default_text: '', is_default: true },
    memory_prompt: { text: '', default_text: '', is_default: true },
  },
  quick_prompts: { items: [], is_default: true },
  tool_limits: {
    max_rounds: { value: 100, default: 100, is_default: true },
    repeat_window_seconds: { value: 30, default: 30, is_default: true },
    repeat_limit: { value: 10, default: 10, is_default: true },
  },
  bash_tool: {
    enabled: { value: true, is_default: true },
    working_dir: { value: '', is_default: true },
    grace_seconds: { value: 5, default: 5, is_default: true },
  },
}

function draft(over: Partial<ToolLimitsDraft> = {}): ToolLimitsDraft {
  return {
    max_rounds: '100',
    repeat_window_seconds: '30',
    repeat_limit: '10',
    bash_grace_seconds: '5',
    ...over,
  }
}

function bash(over: Partial<BashDraft> = {}): BashDraft {
  return { enabled: true, workingDir: '', ...over }
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
    expect(draftError(draft({ bash_grace_seconds: '61' }))).toBe('执行前缓冲要在 0 到 60 之间。')
  })

  it('lets a blank working directory pass but not a relative one', () => {
    expect(workingDirError('')).toBeNull()
    expect(workingDirError('  ')).toBeNull()
    expect(workingDirError('some/relative/path')).toContain('绝对路径')
    expect(workingDirError('C:\\Users\\me\\projects')).toBeNull()
    expect(workingDirError('C:/Users/me/projects')).toBeNull()
  })
})

describe('what a save sends', () => {
  it('sends the parsed numbers', () => {
    expect(toPayload(draft({ max_rounds: ' 50 ' }), bash())).toEqual({
      tool_max_rounds: 50,
      tool_repeat_window_seconds: 30,
      tool_repeat_limit: 10,
      bash_grace_seconds: 5,
      bash_enabled: null,
      bash_working_dir: null,
    })
  })

  it('sends the switch as false and the directory as its text', () => {
    expect(toPayload(draft(), bash({ enabled: false, workingDir: ' D:/work ' }))).toMatchObject({
      bash_enabled: false,
      bash_working_dir: 'D:/work',
    })
  })

  it('sends nulls for 恢复默认', () => {
    expect(TOOL_LIMITS_DEFAULT_PATCH).toEqual({
      tool_max_rounds: null,
      tool_repeat_window_seconds: null,
      tool_repeat_limit: null,
      bash_enabled: null,
      bash_working_dir: null,
      bash_grace_seconds: null,
    })
  })
})

describe('drafting from the overview', () => {
  it('starts from the values in force', () => {
    expect(draftFrom(overview)).toEqual(draft())
    expect(bashDraftFrom(overview)).toEqual(bash())
  })

  it('starts from the stored values once changed', () => {
    const changed: AppSettings = {
      ...overview,
      tool_limits: {
        ...overview.tool_limits,
        max_rounds: { value: 50, default: 100, is_default: false },
      },
      bash_tool: {
        enabled: { value: false, is_default: false },
        working_dir: { value: 'C:/work', is_default: false },
        grace_seconds: { value: 0, default: 5, is_default: false },
      },
    }
    expect(draftFrom(changed)).toEqual(draft({ max_rounds: '50', bash_grace_seconds: '0' }))
    expect(bashDraftFrom(changed)).toEqual(bash({ enabled: false, workingDir: 'C:/work' }))
  })
})
