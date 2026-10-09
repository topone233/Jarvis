/**
 * 单便签详情：点「打开」列表里的一条进来，就地查看/编辑，不离开卡片。
 *
 * 保存约定与 NotepadPage 相同（两处实现，互为参照，改一处要同步另一处）：
 * 编辑落在本地草稿，1.5 秒防抖之后整份 PATCH 上去，base_updated_at 充当
 * 冲突令牌——409 意味着便签在别的窗口（Typora、/notes 页）被改过，页面
 * 给出两个出口而不是静默清掉任何一边。这里刻意没有「新建」态：详情只
 * 面向已有便签，草稿也没有标签段——标签编辑留在整页。
 *
 * 所有的退出路径（返回、Esc、✕）都先经卸载钩子 flush：没落盘的编辑在
 * 组件消失那一刻立刻保存，不丢字。
 */

import { useEffect, useRef, useState } from 'react'

import { ApiError } from '../../../frontend/src/api/client'
import { ArrowLeftIcon, CloseIcon } from '../../../frontend/src/components/icons'
import { useToast } from '../../../frontend/src/hooks/useToast'
import { getNote, updateNote } from './api'
import { NoteEditor } from './NoteEditor'

type SaveState = 'saved' | 'dirty' | 'saving' | 'conflict'

const AUTOSAVE_MS = 1_500

interface Draft {
  title: string
  content: string
}

export function NoteDetail({
  noteId,
  onBack,
  onClose,
}: {
  noteId: string
  onBack(): void
  onClose(): void
}) {
  const toast = useToast()
  const [draft, setDraft] = useState<Draft | null>(null)
  const [saveState, setSaveState] = useState<SaveState>('saved')
  const [editorKey, setEditorKey] = useState(0)

  // The save path runs off refs, mirroring NotepadPage: a debounced timer
  // always touches the newest draft.
  const draftRef = useRef<Draft | null>(null)
  draftRef.current = draft
  const baseRef = useRef('')
  const dirtyRef = useRef(false)
  const timerRef = useRef<number | null>(null)

  // Load the note. A note that vanished (deleted elsewhere) says so and goes
  // back - the list behind is the place to recover from.
  useEffect(() => {
    let alive = true
    getNote(noteId)
      .then((note) => {
        if (!alive) {
          return
        }
        baseRef.current = note.updated_at
        dirtyRef.current = false
        setSaveState('saved')
        setDraft({ title: note.title, content: note.content })
        setEditorKey((key) => key + 1)
      })
      .catch((cause) => {
        if (!alive) {
          return
        }
        toast.show(
          cause instanceof ApiError && cause.status === 404
            ? '这条便签不存在了。'
            : cause instanceof Error
              ? cause.message
              : '这条便签读不出来了。',
          'bad',
        )
        onBack()
      })
    return () => {
      alive = false
    }
    // noteId never changes within a mounted instance: the parent rebuilds us
    // for another note. See the editor's own lifetime note in NoteEditor.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [noteId])

  useEffect(() => () => void flushSave(), [])

  function scheduleSave() {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current)
    }
    timerRef.current = window.setTimeout(() => void save(), AUTOSAVE_MS)
  }

  async function save(): Promise<void> {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current)
      timerRef.current = null
    }
    if (!dirtyRef.current || draftRef.current === null) {
      return
    }
    const snapshot = draftRef.current
    dirtyRef.current = false
    setSaveState('saving')
    try {
      const saved = await updateNote(noteId, {
        title: snapshot.title,
        content: snapshot.content,
        base_updated_at: baseRef.current,
      })
      baseRef.current = saved.updated_at
      setSaveState('saved')
    } catch (cause) {
      dirtyRef.current = true
      if (cause instanceof ApiError && cause.status === 409) {
        setSaveState('conflict')
      } else {
        setSaveState('dirty')
        toast.show('保存失败，稍后会自动重试。', 'bad')
      }
    }
  }

  async function flushSave(): Promise<void> {
    if (dirtyRef.current) {
      await save()
    }
  }

  function markDirty(next: Partial<Draft>) {
    setDraft((current) => (current === null ? current : { ...current, ...next }))
    dirtyRef.current = true
    setSaveState('dirty')
    scheduleSave()
  }

  async function reloadFromDisk() {
    const note = await getNote(noteId)
    baseRef.current = note.updated_at
    dirtyRef.current = false
    setDraft({ title: note.title, content: note.content })
    setEditorKey((key) => key + 1)
    setSaveState('saved')
  }

  async function overwriteAnyway() {
    if (draftRef.current === null) {
      return
    }
    try {
      const saved = await updateNote(noteId, {
        title: draftRef.current.title,
        content: draftRef.current.content,
      })
      baseRef.current = saved.updated_at
      dirtyRef.current = false
      setSaveState('saved')
      toast.show('已按当前编辑内容覆盖保存')
    } catch (cause) {
      toast.show(cause instanceof Error ? cause.message : '保存失败，请再试一次。', 'bad')
    }
  }

  if (draft === null) {
    return <p className="quick-note-empty">加载中…</p>
  }

  const saveLabel =
    saveState === 'saving'
      ? '保存中…'
      : saveState === 'dirty'
        ? '未保存'
        : saveState === 'conflict'
          ? '有冲突'
          : '已保存'

  return (
    <>
      <div className="quick-note-tabs">
        <button type="button" className="icon-button" aria-label="返回列表" title="返回" onClick={onBack}>
          <ArrowLeftIcon size={15} />
        </button>
        <input
          className="quick-note-title"
          value={draft.title}
          placeholder="标题（可选）"
          onChange={(event) => markDirty({ title: event.target.value })}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault()
            }
          }}
        />
        <span className={`notepad-save-state is-${saveState}`}>{saveLabel}</span>
        <button type="button" className="icon-button" aria-label="关闭" onClick={onClose}>
          <CloseIcon size={15} />
        </button>
      </div>
      {saveState === 'conflict' && (
        <div className="form-error notepad-conflict">
          这条便签在保存前被外部修改了（可能在 Typora 或其他编辑器里）。
          <button type="button" className="button button-ghost button-small" onClick={() => void reloadFromDisk()}>
            读取磁盘上的版本
          </button>
          <button type="button" className="button button-ghost button-small" onClick={() => void overwriteAnyway()}>
            用我的版本覆盖
          </button>
        </div>
      )}
      <div className="quick-note-editor">
        <NoteEditor
          key={editorKey}
          value={draft.content}
          onChange={(markdown) => {
            if (draftRef.current !== null && markdown !== draftRef.current.content) {
              markDirty({ content: markdown })
            }
          }}
          placeholder="写点什么……"
        />
      </div>
    </>
  )
}
