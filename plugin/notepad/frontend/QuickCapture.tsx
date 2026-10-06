/**
 * 快速呼出弹窗：全局快捷键唤起，纯文本即记即存。
 *
 * 这是"不打断手头的事"的入口——参考系统便签的样式：新建/打开两个页签、
 * 标题可选、正文自动伸缩、底部字数，保存即关。富编辑留在 /notes 页，
 * 这里刻意只有一只 textarea。
 *
 * 快捷键从插件配置里读（capture_hotkey），插件设置变化时重读；解析不出
 * 有效的组合键就不挂监听，而不是猜一个。
 */

import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router'

import { useToast } from '../../../frontend/src/hooks/useToast'
import { CloseIcon } from '../../../frontend/src/components/icons'
import { createNote, listNotes, readPluginConfig, type NoteSummary } from './api'
import { formatHotkey, hotkeyMatches, parseHotkey, type HotkeyCombo } from '../../../frontend/src/plugins/hotkey'
import { PLUGINS_CHANGED_EVENT } from '../../../frontend/src/plugins/registry'
import { relativeTime } from './time'

const RECENTS_LIMIT = 8

export function QuickCapture() {
  const toast = useToast()
  const navigate = useNavigate()

  const [combo, setCombo] = useState<HotkeyCombo | null>(null)
  const [comboLabel, setComboLabel] = useState('')
  const [open, setOpen] = useState(false)
  const [tab, setTab] = useState<'new' | 'open'>('new')
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [recents, setRecents] = useState<NoteSummary[] | null>(null)
  const [saving, setSaving] = useState(false)

  const titleRef = useRef<HTMLInputElement>(null)
  const areaRef = useRef<HTMLTextAreaElement>(null)

  // The configured summon key, re-read whenever plugin settings change.
  useEffect(() => {
    let alive = true
    async function read() {
      let label = 'Alt+N'
      let parsed: HotkeyCombo | null = parseHotkey('Alt+N')
      try {
        const config = await readPluginConfig()
        const text = typeof config.capture_hotkey === 'string' ? config.capture_hotkey : 'Alt+N'
        parsed = parseHotkey(text)
        label = parsed === null ? text : formatHotkey(parsed)
      } catch {
        // The app is not set up (or the plugin is off) - the shipped default
        // still stands; nothing here is worth an error toast.
      }
      if (alive) {
        setCombo(parsed)
        setComboLabel(label)
      }
    }
    void read()
    window.addEventListener(PLUGINS_CHANGED_EVENT, read)
    return () => {
      alive = false
      window.removeEventListener(PLUGINS_CHANGED_EVENT, read)
    }
  }, [])

  // The summon key toggles the popup from anywhere. `active` holds the
  // narrowed combo: TS will not carry a narrowing into a hoisted function
  // declaration, and onKeyDown below is one.
  useEffect(() => {
    if (combo === null) {
      return
    }
    const active = combo
    function onKeyDown(event: KeyboardEvent) {
      if (hotkeyMatches(active, event)) {
        event.preventDefault()
        setOpen((current) => !current)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [combo])

  // While open: Esc closes (an IME mid-composition Esc belongs to the IME),
  // focus lands in the title, and the 打开 tab's list gets one fresh read.
  useEffect(() => {
    if (!open) {
      return
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape' && !event.isComposing) {
        setOpen(false)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    titleRef.current?.focus()
    listNotes({ limit: RECENTS_LIMIT })
      .then((page) => setRecents(page.items))
      .catch(() => setRecents([]))
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [open])

  // Grow with the content, capped so a pasted essay cannot cover the screen.
  useEffect(() => {
    const area = areaRef.current
    if (area === null) {
      return
    }
    area.style.height = 'auto'
    area.style.height = `${Math.min(area.scrollHeight, 320)}px`
  }, [content, open])

  const canSave = title.trim() !== '' || content.trim() !== ''
  const firstLine = content.trim().split('\n')[0]?.slice(0, 16) ?? ''

  async function save() {
    if (saving) {
      return
    }
    setSaving(true)
    try {
      const note = await createNote({ title: title.trim(), content, source: 'popup' })
      toast.show(note.tag_status === 'pending' ? '已保存，AI 打标签中…' : '已保存')
      setTitle('')
      setContent('')
      setTab('new')
      setRecents(null)
      setOpen(false)
    } catch (cause) {
      toast.show(cause instanceof Error ? cause.message : '保存失败，请再试一次。', 'bad')
    } finally {
      setSaving(false)
    }
  }

  function openNote(noteId: string) {
    setOpen(false)
    navigate(`/notes?note=${noteId}`)
  }

  if (!open) {
    return null
  }

  return (
    <div
      className="quick-note"
      onClick={(event) => {
        if (event.target === event.currentTarget) {
          setOpen(false)
        }
      }}
    >
      <div className="quick-note-box" role="dialog" aria-label="快速记一条">
        <div className="quick-note-tabs">
          <button
            type="button"
            className={`quick-note-tab${tab === 'new' ? ' is-on' : ''}`}
            onClick={() => setTab('new')}
          >
            新建
          </button>
          <button
            type="button"
            className={`quick-note-tab${tab === 'open' ? ' is-on' : ''}`}
            onClick={() => setTab('open')}
          >
            打开
          </button>
          <span className="spacer" />
          <span className="quick-note-hotkey">{comboLabel}</span>
          <button
            type="button"
            className="icon-button"
            aria-label="关闭"
            onClick={() => setOpen(false)}
          >
            <CloseIcon size={15} />
          </button>
        </div>

        {tab === 'new' ? (
          <>
            <input
              ref={titleRef}
              className="quick-note-title"
              value={title}
              placeholder="标题（可选）"
              onChange={(event) => setTitle(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault()
                  areaRef.current?.focus()
                }
              }}
            />
            <textarea
              ref={areaRef}
              className="quick-note-content"
              value={content}
              placeholder="写点什么……"
              onChange={(event) => setContent(event.target.value)}
            />
            <div className="quick-note-foot">
              <span className="quick-note-count">
                {content.length} 字 · {firstLine === '' ? '空' : firstLine}
              </span>
              <span className="spacer" />
              <button
                type="button"
                className="button button-ghost button-small"
                disabled={!canSave || saving}
                onClick={() => {
                  setTitle('')
                  setContent('')
                  titleRef.current?.focus()
                }}
              >
                清空
              </button>
              <button
                type="button"
                className="button button-primary button-small"
                disabled={!canSave || saving}
                onClick={() => void save()}
              >
                {saving ? '保存中…' : '保存'}
              </button>
            </div>
          </>
        ) : (
          <div className="quick-note-recents">
            {recents === null && <p className="quick-note-empty">加载中…</p>}
            {recents !== null && recents.length === 0 && (
              <p className="quick-note-empty">还没有便签。</p>
            )}
            {recents?.map((note) => (
              <button
                key={note.id}
                type="button"
                className="quick-note-row"
                onClick={() => openNote(note.id)}
              >
                <span className="quick-note-row-title">{note.title || note.excerpt || '（无标题）'}</span>
                <span className="quick-note-row-time">{relativeTime(note.updated_at)}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
