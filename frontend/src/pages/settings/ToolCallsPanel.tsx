/**
 * The tool-calls tab: how much tool work one answer may do, and the bash
 * tool's ground rules.
 *
 * The screen edits a local draft - the three budget numbers, plus the bash
 * switch, its working directory, and the grace window before each command;
 * 保存 parses and sends them together, and 恢复默认 - like on the prompts
 * tab, because "put the shipped values back" has no draft shape worth showing
 * between the click and the save - sends nulls immediately and throws away
 * what was typed. Values are read by the next run, not the one already going.
 */

import { useEffect, useState } from 'react'

import { getSettings, saveSettings } from '../../api/endpoints'
import { ApiError } from '../../api/client'
import {
  bashDraftFrom,
  draftError,
  draftFrom,
  labelOf,
  TOOL_LIMITS,
  TOOL_LIMITS_DEFAULT_PATCH,
  toPayload,
  workingDirError,
} from '../../api/toolLimits'
import type { BashDraft, ToolLimitKey, ToolLimitsDraft } from '../../api/toolLimits'

const HINTS: Record<ToolLimitKey, string> = {
  max_rounds: '一次回答里，模型可以连续使用工具的轮数上限。用完就结束本次回答。',
  repeat_window_seconds: '同一调用（工具名和参数都相同）在这段时间内的执行次数会被累计。',
  repeat_limit: '窗口内同一调用最多执行的次数，超过后的那次不会执行，模型会收到提示。',
  bash_grace_seconds:
    '每条 bash 命令执行前等待的秒数，期间可以随时点停止按钮拦下它。0 表示不等待。',
}

/** The three budget numbers draw first; the bash grace input draws inside
 *  the bash group, next to the things it belongs to. */
const BUDGET_KEYS: ToolLimitKey[] = ['max_rounds', 'repeat_window_seconds', 'repeat_limit']

export function ToolCallsPanel() {
  const [draft, setDraft] = useState<ToolLimitsDraft | null>(null)
  const [bash, setBash] = useState<BashDraft | null>(null)
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
        const nextDraft = draftFrom(settings)
        const nextBash = bashDraftFrom(settings)
        setDraft(nextDraft)
        setBash(nextBash)
        setStored(serialize(nextDraft, nextBash))
        setIsDefault(
          settings.tool_limits.max_rounds.is_default &&
            settings.tool_limits.repeat_window_seconds.is_default &&
            settings.tool_limits.repeat_limit.is_default &&
            settings.bash_tool.enabled.is_default &&
            settings.bash_tool.working_dir.is_default &&
            settings.bash_tool.grace_seconds.is_default,
        )
      })
      .catch((cause: unknown) => setError(describe(cause)))
  }, [])

  if (draft === null || bash === null) {
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

  const changed = serialize(draft, bash) !== stored

  const edit = (key: ToolLimitKey, text: string) => {
    setDraft({ ...draft, [key]: text })
    setNote(null)
  }

  const editBash = (patch: Partial<BashDraft>) => {
    setBash({ ...bash, ...patch })
    setNote(null)
  }

  const save = async () => {
    const problem = draftError(draft) ?? workingDirError(bash.workingDir)
    if (problem !== null) {
      setNote(null)
      setError(problem)
      return
    }
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      const settings = await saveSettings(toPayload(draft, bash))
      const nextDraft = draftFrom(settings)
      const nextBash = bashDraftFrom(settings)
      setDraft(nextDraft)
      setBash(nextBash)
      setStored(serialize(nextDraft, nextBash))
      setIsDefault(
        settings.tool_limits.max_rounds.is_default &&
          settings.tool_limits.repeat_window_seconds.is_default &&
          settings.tool_limits.repeat_limit.is_default &&
          settings.bash_tool.enabled.is_default &&
          settings.bash_tool.working_dir.is_default &&
          settings.bash_tool.grace_seconds.is_default,
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
      const nextDraft = draftFrom(settings)
      const nextBash = bashDraftFrom(settings)
      setDraft(nextDraft)
      setBash(nextBash)
      setStored(serialize(nextDraft, nextBash))
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
        模型回答问题时可以连续调用工具，这里决定它最多能用多少次，以及同一个调用短时间里反复出现时何时拦下；
        bash 工具也在这里配置。
      </p>
      {BUDGET_KEYS.map((key) => (
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
      <div className="field">
        <label>bash 工具</label>
        <button
          type="button"
          role="switch"
          aria-checked={bash.enabled}
          aria-label={bash.enabled ? '停用 bash 工具' : '启用 bash 工具'}
          className={`skill-switch${bash.enabled ? ' is-on' : ''}`}
          title={bash.enabled ? '已启用' : '已停用'}
          onClick={() => editBash({ enabled: !bash.enabled })}
        >
          <span className="skill-knob" />
        </button>
        <span className="hint">
          给模型一个在本机执行 shell 命令的工具。它调用的每条命令都会显示在回答上方的步骤里。
        </span>
      </div>
      <div className="field">
        <label htmlFor="bash-working-dir">工作目录</label>
        <input
          id="bash-working-dir"
          type="text"
          value={bash.workingDir}
          placeholder="数据目录"
          onChange={(event) => editBash({ workingDir: event.target.value })}
        />
        <span className="hint">
          bash 命令在这里执行；留空表示数据目录。必须是已存在的绝对路径。
        </span>
      </div>
      <div className="field">
        <label htmlFor="tool-limit-bash_grace_seconds">{labelOf('bash_grace_seconds')}</label>
        <input
          id="tool-limit-bash_grace_seconds"
          type="number"
          min={TOOL_LIMITS.bash_grace_seconds.min}
          max={TOOL_LIMITS.bash_grace_seconds.max}
          value={draft.bash_grace_seconds}
          onChange={(event) => edit('bash_grace_seconds', event.target.value)}
        />
        <span className="hint">{HINTS.bash_grace_seconds}</span>
      </div>
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
              title="丢掉这里的修改，回到内置的值"
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
function serialize(draft: ToolLimitsDraft, bash: BashDraft): string {
  return JSON.stringify([
    draft.max_rounds.trim(),
    draft.repeat_window_seconds.trim(),
    draft.repeat_limit.trim(),
    draft.bash_grace_seconds.trim(),
    bash.enabled,
    bash.workingDir.trim(),
  ])
}

function describe(cause: unknown): string {
  if (cause instanceof ApiError || cause instanceof Error) {
    return cause.message
  }
  return '出了点问题，请再试一次。'
}
