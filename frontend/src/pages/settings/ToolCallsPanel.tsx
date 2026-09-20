/**
 * The tool-calls tab: how much tool work one answer may do.
 *
 * The screen edits a local draft of three numbers; 保存 parses and sends
 * them together, and 恢复默认 - like on the prompts tab, because "put the
 * shipped numbers back" has no draft shape worth showing between the click
 * and the save - sends three nulls immediately and throws away what was
 * typed. Values are read by the next run, not the one already going.
 */

import { useEffect, useState } from 'react'

import { getSettings, saveSettings } from '../../api/endpoints'
import { ApiError } from '../../api/client'
import {
  draftError,
  draftFrom,
  labelOf,
  TOOL_LIMITS,
  TOOL_LIMITS_DEFAULT_PATCH,
  toPayload,
} from '../../api/toolLimits'
import type { ToolLimitKey, ToolLimitsDraft } from '../../api/toolLimits'
const HINTS: Record<ToolLimitKey, string> = {
  max_rounds: '一次回答里，模型可以连续使用工具的轮数上限。用完就结束本次回答。',
  repeat_window_seconds: '同一调用（工具名和参数都相同）在这段时间内的执行次数会被累计。',
  repeat_limit: '窗口内同一调用最多执行的次数，超过后的那次不会执行，模型会收到提示。',
}

export function ToolCallsPanel() {
  const [draft, setDraft] = useState<ToolLimitsDraft | null>(null)
  // The draft as the server has it, serialized - the one-line answer to
  // "has anything been typed".
  const [stored, setStored] = useState<string | null>(null)
  const [isDefault, setIsDefault] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  useEffect(() => {
    void getSettings()
      .then((settings) => {
        setDraft(draftFrom(settings.tool_limits))
        setStored(serializeDraft(draftFrom(settings.tool_limits)))
        setIsDefault(
          settings.tool_limits.max_rounds.is_default &&
            settings.tool_limits.repeat_window_seconds.is_default &&
            settings.tool_limits.repeat_limit.is_default,
        )
      })
      .catch((cause: unknown) => setError(describe(cause)))
  }, [])

  if (draft === null) {
    return (
      <section className="setup-section">
        {error === null ? (
          <p className="setup-empty">正在读取…</p>
        ) : (
          <div className="form-error">{error}</div>
        )}
      </section>
    )
  }

  const changed = serializeDraft(draft) !== stored

  const edit = (key: ToolLimitKey, text: string) => {
    setDraft({ ...draft, [key]: text })
    setNote(null)
  }

  const save = async () => {
    const problem = draftError(draft)
    if (problem !== null) {
      setNote(null)
      setError(problem)
      return
    }
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      const settings = await saveSettings(toPayload(draft))
      setDraft(draftFrom(settings.tool_limits))
      setStored(serializeDraft(draftFrom(settings.tool_limits)))
      setIsDefault(
        settings.tool_limits.max_rounds.is_default &&
          settings.tool_limits.repeat_window_seconds.is_default &&
          settings.tool_limits.repeat_limit.is_default,
      )
      setNote('已保存。')
    } catch (cause) {
      setError(describe(cause))
    } finally {
      setBusy(false)
    }
  }

  const restoreDefault = async () => {
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      const settings = await saveSettings(TOOL_LIMITS_DEFAULT_PATCH)
      setDraft(draftFrom(settings.tool_limits))
      setStored(serializeDraft(draftFrom(settings.tool_limits)))
      setIsDefault(true)
      setNote('已恢复默认。')
    } catch (cause) {
      setError(describe(cause))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="setup-section">
      <p className="quick-intro">
        模型回答问题时可以连续调用工具，这里决定它最多能用多少次，以及同一个调用短时间里反复出现时何时拦下。
      </p>
      {(Object.keys(TOOL_LIMITS) as ToolLimitKey[]).map((key) => (
        <div className="field" key={key}>
          <label htmlFor={`tool-limit-${key}`}>{labelOf(key)}</label>
          <input
            id={`tool-limit-${key}`}
            type="number"
            min={TOOL_LIMITS[key].min}
            max={TOOL_LIMITS[key].max}
            value={draft[key]}
            onChange={(event) => edit(key, event.target.value)}
          />
          <span className="hint">{HINTS[key]}</span>
        </div>
      ))}
      <div className="setup-actions">
        {isDefault ? (
          <span className="badge badge-quiet">默认</span>
        ) : (
          <>
            <span className="badge">已改过</span>
            <button
              type="button"
              className="button button-ghost button-small"
              disabled={busy}
              title="丢掉这里的修改，回到内置的三个数"
              onClick={() => void restoreDefault()}
            >
              恢复默认
            </button>
          </>
        )}
        <span className="spacer" />
        <button
          type="button"
          className="button button-primary"
          disabled={busy || !changed}
          onClick={() => void save()}
        >
          保存
        </button>
      </div>
      {error !== null && <div className="form-error">{error}</div>}
      {note !== null && <div className="form-ok">{note}</div>}
    </section>
  )
}

/** The draft as the store holds it - trimmed - so `changed` can compare. */
function serializeDraft(draft: ToolLimitsDraft): string {
  return JSON.stringify([
    draft.max_rounds.trim(),
    draft.repeat_window_seconds.trim(),
    draft.repeat_limit.trim(),
  ])
}

function describe(cause: unknown): string {
  if (cause instanceof ApiError || cause instanceof Error) {
    return cause.message
  }
  return '出了点问题，请再试一次。'
}
