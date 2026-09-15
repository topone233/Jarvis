/**
 * The quick-prompts tab: the buttons an empty composer offers.
 *
 * The screen edits a local draft; 保存 sends the whole list at once, in draw
 * order, because with a list this small per-row saves would only make
 * ordering and deletion harder to reason about. Nothing reaches the database
 * until then, so deleting a row or clearing the list asks nothing, and
 * leaving the tab throws the draft away - the same bargain the prompts tab
 * makes.
 *
 * 恢复默认 is not a staged edit, for the same reason it is not on the prompts
 * tab: "put the built-in pair back" has no draft shape worth showing between
 * the click and the save, so it saves immediately and throws away whatever
 * was typed.
 *
 * Every handler is an arrow defined after the null check and reads this
 * render's `items` directly, rather than reaching through a setState updater:
 * the row it touches is the row on screen, and one setItems per event needs
 * no batching.
 */

import { useEffect, useState } from 'react'

import { getSettings, saveSettings } from '../../api/endpoints'
import { ApiError } from '../../api/client'
import {
  draftError,
  moved,
  NAME_MAX,
  PROMPT_MAX,
  QUICK_PROMPT_MAX,
  toPayload,
} from '../../api/quickPrompts'
import type { QuickPrompt } from '../../api/types'

export function QuickPromptsPanel() {
  const [items, setItems] = useState<QuickPrompt[] | null>(null)
  // The list as the server has it, serialized - the one-line answer to "has
  // anything been typed", for a value whose shape is just a string list.
  const [stored, setStored] = useState<string | null>(null)
  const [isDefault, setIsDefault] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  useEffect(() => {
    void getSettings()
      .then((settings) => {
        setItems(settings.quick_prompts.items)
        setStored(JSON.stringify(settings.quick_prompts.items))
        setIsDefault(settings.quick_prompts.is_default)
      })
      .catch((cause: unknown) => setError(describe(cause)))
  }, [])

  if (items === null) {
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

  const changed = JSON.stringify(items) !== stored

  // `field` is spelled out in the two branches rather than used as a computed
  // key: a union computed key widens the object literal to an index signature,
  // which is not the row this list is made of.
  const edit = (index: number, field: 'name' | 'prompt', text: string) => {
    setItems(
      items.map((item, at) => {
        if (at !== index) {
          return item
        }
        return field === 'name' ? { ...item, name: text } : { ...item, prompt: text }
      }),
    )
    setNote(null)
  }

  const save = async () => {
    const problem = draftError(items)
    if (problem !== null) {
      setNote(null)
      setError(problem)
      return
    }
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      // The response is the whole screen's new state, so the rows are redrawn
      // from it rather than from an assumption about what the save did.
      const settings = await saveSettings(toPayload(items))
      setItems(settings.quick_prompts.items)
      setStored(JSON.stringify(settings.quick_prompts.items))
      setIsDefault(settings.quick_prompts.is_default)
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
      const settings = await saveSettings({ quick_prompts: null })
      setItems(settings.quick_prompts.items)
      setStored(JSON.stringify(settings.quick_prompts.items))
      setIsDefault(settings.quick_prompts.is_default)
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
        输入框为空时，这些会以按钮的形式出现在输入框上方；点击后把提示词填进去，内容自己补。
        全部删掉再保存，就是不显示任何按钮。
      </p>
      {items.map((item, index) => (
        <div className="quick-row" key={index}>
          <input
            value={item.name}
            placeholder="按钮名称"
            maxLength={NAME_MAX}
            aria-label={`第 ${index + 1} 条的名称`}
            onChange={(event) => edit(index, 'name', event.target.value)}
          />
          <input
            value={item.prompt}
            placeholder="点击后填进输入框的提示词"
            maxLength={PROMPT_MAX}
            aria-label={`第 ${index + 1} 条的提示词`}
            onChange={(event) => edit(index, 'prompt', event.target.value)}
          />
          <span className="quick-row-actions">
            <button
              type="button"
              className="icon-button"
              title="上移"
              aria-label={`第 ${index + 1} 条上移`}
              disabled={index === 0}
              onClick={() => setItems(moved(items, index, -1))}
            >
              ↑
            </button>
            <button
              type="button"
              className="icon-button"
              title="下移"
              aria-label={`第 ${index + 1} 条下移`}
              disabled={index === items.length - 1}
              onClick={() => setItems(moved(items, index, 1))}
            >
              ↓
            </button>
            <button
              type="button"
              className="icon-button"
              title="删除这一条"
              aria-label={`删除第 ${index + 1} 条`}
              onClick={() => setItems(items.filter((_, at) => at !== index))}
            >
              ✕
            </button>
          </span>
        </div>
      ))}
      <div className="setup-actions">
        <button
          type="button"
          className="button button-ghost button-small"
          disabled={busy || items.length >= QUICK_PROMPT_MAX}
          onClick={() => setItems([...items, { name: '', prompt: '' }])}
        >
          添加一条
        </button>
        {isDefault ? (
          <span className="badge badge-quiet">默认</span>
        ) : (
          <>
            <span className="badge">已改过</span>
            <button
              type="button"
              className="button button-ghost button-small"
              disabled={busy}
              title="丢掉这里的修改，回到内置的那两条"
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

function describe(cause: unknown): string {
  if (cause instanceof ApiError || cause instanceof Error) {
    return cause.message
  }
  return '出了点问题，请再试一次。'
}
