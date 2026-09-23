/**
 * The tool-calls tab's rules, kept out of the component and tested.
 *
 * Three numbers decide how much tool work one answer may do: how many rounds
 * the loop may spend, and how many times the same call - same tool, same
 * arguments - may execute inside a time window before the next one is turned
 * away. The bash tool drafts a fourth number (the grace window before each
 * command), its on/off switch, and the directory commands start in. The draft
 * is the raw text of the inputs, so a half-typed value is a draft state and
 * not a bug; only 保存 parses, and the first problem becomes an inline
 * sentence instead of a 422. The API holds the same caps.
 */

import type { AppSettings, SettingsPatch, ToolLimitKey } from './types'

export type { ToolLimitKey } from './types'

/** The caps the API's schema enforces too, mirrored so the form cannot offer
 *  a value the store would reject. */
export const TOOL_LIMITS: Record<ToolLimitKey, { min: number; max: number }> = {
  max_rounds: { min: 1, max: 1000 },
  repeat_window_seconds: { min: 1, max: 3600 },
  repeat_limit: { min: 1, max: 1000 },
  bash_grace_seconds: { min: 0, max: 60 },
}

/** One input's draft: what the box shows, which may be half-typed. */
export type ToolLimitsDraft = Record<ToolLimitKey, string>

/** The bash switch, directory, and how a command gets to run, drafted. */
export interface BashDraft {
  enabled: boolean
  workingDir: string
  /** "ask" holds each command for the user's approval; "grace" is the fixed
   *  buffer, whose length the bash_grace_seconds input owns. */
  approvalMode: 'ask' | 'grace'
}

export function draftFrom(settings: AppSettings): ToolLimitsDraft {
  return {
    max_rounds: String(settings.tool_limits.max_rounds.value),
    repeat_window_seconds: String(settings.tool_limits.repeat_window_seconds.value),
    repeat_limit: String(settings.tool_limits.repeat_limit.value),
    bash_grace_seconds: String(settings.bash_tool.grace_seconds.value),
  }
}

export function bashDraftFrom(settings: AppSettings): BashDraft {
  return {
    enabled: settings.bash_tool.enabled.value,
    workingDir: settings.bash_tool.working_dir.value,
    approvalMode: settings.bash_tool.approval_mode.value,
  }
}

const WINDOWS_ABSOLUTE_PATH = /^[a-zA-Z]:[\\/]|^\\\\/

/** The working directory's one client-side rule: filled or not, and if filled
 *  it must look absolute. Whether it exists is the server's check, so the
 *  error can name the real directory when it is not. */
export function workingDirError(value: string): string | null {
  const text = value.trim()
  if (text === '') {
    return null
  }
  return WINDOWS_ABSOLUTE_PATH.test(text)
    ? null
    : '工作目录要是本机的绝对路径，例如 C:\\Users\\me\\projects。'
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
    bash_grace_seconds: '执行前缓冲',
  }[key]
}

/** The draft as a save body, once draftError has said it would save. */
export function toPayload(draft: ToolLimitsDraft, bash: BashDraft): SettingsPatch {
  return {
    tool_max_rounds: Number(draft.max_rounds.trim()),
    tool_repeat_window_seconds: Number(draft.repeat_window_seconds.trim()),
    tool_repeat_limit: Number(draft.repeat_limit.trim()),
    bash_grace_seconds: Number(draft.bash_grace_seconds.trim()),
    // On IS the default, so enabling sends null - deleting the row - instead
    // of storing a stale copy of the default, the same deal the skills list
    // makes. A blank directory is "no row": commands start in the data
    // directory. The mode is the same deal: "ask" IS the default.
    bash_enabled: bash.enabled ? null : false,
    bash_working_dir: bash.workingDir.trim() === '' ? null : bash.workingDir.trim(),
    bash_approval_mode: bash.approvalMode === 'ask' ? null : bash.approvalMode,
  }
}

/** 恢复默认's body: nulls, which delete the rows. */
export const TOOL_LIMITS_DEFAULT_PATCH: SettingsPatch = {
  tool_max_rounds: null,
  tool_repeat_window_seconds: null,
  tool_repeat_limit: null,
  bash_enabled: null,
  bash_working_dir: null,
  bash_grace_seconds: null,
  bash_approval_mode: null,
}
