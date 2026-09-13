/**
 * The prompts tab: the three texts that decide how Jarvis behaves, editable.
 *
 * The screen is deliberately dumb about what the text says - it is a textarea
 * and a save button - because the alternative is a prompt editor with its own
 * syntax rules, and nothing here needs that. What it *is* careful about is which
 * prompts get sent: only the ones that differ from what is stored, so editing
 * the system prompt cannot rewrite the memory prompt with a stale copy.
 *
 * Restoring is not a staged edit. "Put this back" has no text to show in a box
 * between pressing it and saving, so it is a save of its own, and it throws away
 * whatever was typed in that box - which is what the button says it does.
 */

import { useEffect, useState } from 'react'

import { getSettings, saveSettings } from '../../api/endpoints'
import { ApiError } from '../../api/client'
import { changedPrompts, PROMPT_FIELDS, restorePatch, shownText } from '../../api/prompts'
import type { AppSettings, PromptKey, PromptPatch } from '../../api/types'

export function PromptsPanel() {
  const [settings, setSettings] = useState<AppSettings | null>(null)
  const [drafts, setDrafts] = useState<Record<string, string | undefined>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  useEffect(() => {
    void getSettings()
      .then(setSettings)
      .catch((cause: unknown) => setError(describe(cause)))
  }, [])

  async function save(patch: PromptPatch) {
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      // The response is the whole screen's new state, so the boxes are redrawn
      // from it rather than from an assumption about what the save did.
      setSettings(await saveSettings(patch))
      setDrafts({})
      setNote('已保存。')
    } catch (cause) {
      setError(describe(cause))
    } finally {
      setBusy(false)
    }
  }

  if (settings === null) {
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

  const patch = changedPrompts(settings.prompts, drafts)
  const changed = Object.keys(patch).length > 0
  const edit = (key: PromptKey, text: string) => {
    setDrafts((open) => ({ ...open, [key]: text }))
    setNote(null)
  }

  return (
    <section className="setup-section">
      {PROMPT_FIELDS.map((field) => {
        const setting = settings.prompts[field.key]
        return (
          <div className="field" key={field.key}>
            <div className="field-head">
              <label htmlFor={`prompt-${field.key}`}>{field.title}</label>
              {setting.is_default ? (
                <span className="badge badge-quiet">默认</span>
              ) : (
                <>
                  <span className="badge">已改过</span>
                  <button
                    type="button"
                    className="button button-ghost button-small"
                    disabled={busy}
                    title="丢掉这里的修改，回到内置的那一份"
                    onClick={() => void save(restorePatch(field.key))}
                  >
                    恢复默认
                  </button>
                </>
              )}
            </div>
            <textarea
              id={`prompt-${field.key}`}
              className="prompt-box"
              rows={field.key === 'system_prompt' ? 10 : 6}
              value={shownText(settings.prompts, drafts, field.key)}
              // A spelling aid would be wrong here twice over: these are prompts,
              // they are written in two languages, and the red squiggles would be
              // under every Chinese word.
              spellCheck={false}
              onChange={(event) => edit(field.key, event.target.value)}
            />
            <span className="hint">{field.hint}</span>
          </div>
        )
      })}
      <div className="setup-actions">
        <span className="spacer" />
        <button
          type="button"
          className="button button-primary"
          disabled={busy || !changed}
          onClick={() => void save(patch)}
        >
          {changed ? `保存 ${Object.keys(patch).length} 处改动` : '没有改动'}
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
