/**
 * The tool-calls tab's rules, kept out of the component and tested.
 *
 * Three numbers decide how much tool work one answer may do: how many rounds
 * the loop may spend, and how many times the same call - same tool, same
 * arguments - may execute inside a time window before the next one is turned
 * away. The draft is the raw text of three inputs, so a half-typed value is a
 * draft state and not a bug; only 保存 parses, and the first problem becomes
 * an inline sentence instead of a 422. The API holds the same caps.
 */

import type { AppSettings, SettingsPatch, ToolLimitKey } from './types'

export type { ToolLimitKey } from './types'

/** The caps the API's schema enforces too, mirrored so the form cannot offer
 *  a value the store would reject. */
export const TOOL_LIMITS: Record<ToolLimitKey, { min: number; max: number }> = {
  max_rounds: { min: 1, max: 1000 },
  repeat_window_seconds: { min: 1, max: 3600 },
  repeat_limit: { min: 1, max: 1000 },
}

/** One input's draft: what the box shows, which may be half-typed. */
export type ToolLimitsDraft = Record<ToolLimitKey, string>

export function draftFrom(settings: AppSettings['tool_limits']): ToolLimitsDraft {
  return {
    max_rounds: String(settings.max_rounds.value),
    repeat_window_seconds: String(settings.repeat_window_seconds.value),
    repeat_limit: String(settings.repeat_limit.value),
  }
}

/** The first problem in the draft, or null when it would save. */
export function draftError(draft: ToolLimitsDraft): string | null {
  for (const key of Object.keys(TOOL_LIMITS) as ToolLimitKey[]) {
    const text = draft[key].trim()
    if (text === '') {
      return '还有一个值没有填。'
    }
    const value = Number(text)
    if (!Number.isInteger(value)) {
      return '这几个值都要是整数。'
    }
    const { min, max } = TOOL_LIMITS[key]
    if (value < min || value > max) {
      return `${labelOf(key)}要在 ${min} 到 ${max} 之间。`
    }
  }
  return null
}

export function labelOf(key: ToolLimitKey): string {
  return {
    max_rounds: '工具轮次上限',
    repeat_window_seconds: '判定窗口',
    repeat_limit: '次数上限',
  }[key]
}

/** The draft as a save body, once draftError has said it would save. */
export function toPayload(draft: ToolLimitsDraft): SettingsPatch {
  return {
    tool_max_rounds: Number(draft.max_rounds.trim()),
    tool_repeat_window_seconds: Number(draft.repeat_window_seconds.trim()),
    tool_repeat_limit: Number(draft.repeat_limit.trim()),
  }
}

/** 恢复默认's body: three nulls, which delete the rows. */
export const TOOL_LIMITS_DEFAULT_PATCH: SettingsPatch = {
  tool_max_rounds: null,
  tool_repeat_window_seconds: null,
  tool_repeat_limit: null,
}
