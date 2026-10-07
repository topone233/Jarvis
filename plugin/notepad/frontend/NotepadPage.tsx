/**
 * 便签页：左边检索与列表，右边 Typora 风格的编辑器。
 *
 * 保存模型是"后台草稿"：编辑落在一个本地草稿上，1.5 秒的防抖之后整份
 * PATCH 上去，base_updated_at 充当冲突令牌——409 意味着文件在 Typora
 * 或别的窗口里被改过，页面提供重载或强行覆盖，绝不静默清掉任何一边。
 * 新建不预先建文件：第一份草稿在第一次自动保存时才落盘。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'

import { ApiError } from '../../../frontend/src/api/client'
import { useConfirm } from '../../../frontend/src/hooks/useConfirm'
import { useToast } from '../../../frontend/src/hooks/useToast'
import { CloseIcon, PlusIcon, SearchIcon, TrashIcon } from '../../../frontend/src/components/icons'
import { toggleMaximize } from '../../../frontend/src/plugins/shell'
import {
  createNote,
  deleteNote,
  getNote,
  listDeletedNotes,
  listNotes,
  listTags,
  purgeNote,
  retagNote,
  restoreNote,
  updateNote,
  type NoteFull,
  type NoteSummary,
  type TagCount,
} from './api'
import { NoteEditor } from './NoteEditor'
import { relativeTime } from './time'

type SaveState = 'saved' | 'dirty' | 'saving' | 'conflict'

const AUTOSAVE_MS = 1_500
const SEARCH_DEBOUNCE_MS = 300

interface Draft {
  title: string
  content: string
  tags: string[]
}

const EMPTY_DRAFT: Draft = { title: '', content: '', tags: [] }

export default function NotepadPage({ onClose }: { onClose(): void }) {
  const confirm = useConfirm()
  const toast = useToast()
  const [searchParams, setSearchParams] = useSearchParams()

  const [notes, setNotes] = useState<NoteSummary[] | null>(null)
  const [tags, setTags] = useState<TagCount[]>([])
  const [queryInput, setQueryInput] = useState('')
  const [search, setSearch] = useState('')
  const [activeTag, setActiveTag] = useState<string | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [selected, setSelected] = useState<NoteFull | null>(null)
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT)
  const [creating, setCreating] = useState(false)
  const [saveState, setSaveState] = useState<SaveState>('saved')
  const [editorKey, setEditorKey] = useState(0)
  const [deleted, setDeleted] = useState<NoteSummary[] | null>(null)
  const [showDeleted, setShowDeleted] = useState(false)

  // The save path runs off refs so a debounced timer always touches the
  // newest draft, and a note switch cannot let an in-flight save write the
  // next note's base timestamp.
  const draftRef = useRef(draft)
  draftRef.current = draft
  const baseRef = useRef('')
  const selectedIdRef = useRef<string | null>(null)
  const dirtyRef = useRef(false)
  const timerRef = useRef<number | null>(null)

  const refreshList = useCallback(async () => {
    try {
      const [items, tagItems] = await Promise.all([
        listNotes({ q: search || undefined, tag: activeTag ?? undefined }),
        listTags(),
      ])
      setNotes(items.items)
      setTags(tagItems.items)
      setLoadError(null)
    } catch (cause) {
      setLoadError(describe(cause))
    }
  }, [search, activeTag])

  useEffect(() => {
    void refreshList()
  }, [refreshList])

  useEffect(() => {
    if (search === '') {
      return
    }
    const timer = window.setTimeout(() => setSearch(queryInput), SEARCH_DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [queryInput, search])

  // A deep link (?note=…) from the quick-capture popup's 打开 tab.
  const initialNote = useRef(searchParams.get('note'))
  useEffect(() => {
    const id = initialNote.current
    if (id !== null) {
      void select(id)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

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
    if (!dirtyRef.current) {
      return
    }
    const snapshot = draftRef.current
    dirtyRef.current = false
    setSaveState('saving')
    try {
      if (creating) {
        const created = await createNote({
          title: snapshot.title,
          content: snapshot.content,
          source: 'page',
        })
        setCreating(false)
        selectedIdRef.current = created.id
        baseRef.current = created.updated_at
        setSelected({ ...created })
        setSaveState('saved')
        void refreshList()
        setSearchParams({ note: created.id }, { replace: true })
        return
      }
      const id = selectedIdRef.current
      if (id === null) {
        return
      }
      const saved = await updateNote(id, {
        title: snapshot.title,
        content: snapshot.content,
        tags: snapshot.tags,
        base_updated_at: baseRef.current,
      })
      if (selectedIdRef.current !== id) {
        return // switched away mid-save; the next note's base must not move
      }
      baseRef.current = saved.updated_at
      setSelected({ ...saved })
      setSaveState('saved')
      void refreshList()
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
    setDraft((current) => ({ ...current, ...next }))
    dirtyRef.current = true
    setSaveState('dirty')
    scheduleSave()
  }

  async function select(id: string) {
    await flushSave()
    try {
      const note = await getNote(id)
      selectedIdRef.current = note.id
      baseRef.current = note.updated_at
      dirtyRef.current = false
      setSaveState('saved')
      setSelected(note)
      setDraft({ title: note.title, content: note.content, tags: [...note.tags] })
      setEditorKey((key) => key + 1)
      setSearchParams({ note: id }, { replace: true })
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 404) {
        toast.show('这条便签不存在了。', 'bad')
        void refreshList()
      } else {
        toast.show(describe(cause), 'bad')
      }
    }
  }

  function startNew() {
    void flushSave()
    selectedIdRef.current = null
    dirtyRef.current = false
    setCreating(true)
    setSelected(null)
    setDraft(EMPTY_DRAFT)
    setSaveState('saved')
    setEditorKey((key) => key + 1)
    setSearchParams({}, { replace: true })
  }

  async function reloadFromDisk() {
    const id = selectedIdRef.current
    if (id === null) {
      return
    }
    const note = await getNote(id)
    baseRef.current = note.updated_at
    dirtyRef.current = false
    setSelected(note)
    setDraft({ title: note.title, content: note.content, tags: [...note.tags] })
    setEditorKey((key) => key + 1)
    setSaveState('saved')
  }

  async function overwriteAnyway() {
    const id = selectedIdRef.current
    if (id === null) {
      return
    }
    try {
      const saved = await updateNote(id, {
        title: draftRef.current.title,
        content: draftRef.current.content,
        tags: draftRef.current.tags,
      })
      baseRef.current = saved.updated_at
      dirtyRef.current = false
      setSelected({ ...saved })
      setSaveState('saved')
      toast.show('已按当前编辑内容覆盖保存')
      void refreshList()
    } catch (cause) {
      toast.show(describe(cause), 'bad')
    }
  }

  async function removeCurrent() {
    const id = selectedIdRef.current
    if (id === null) {
      return
    }
    const confirmed = await confirm.ask({
      title: '删除这条便签？',
      body: '会移到便签回收站，随时可以恢复。',
      confirmLabel: '删除',
    })
    if (!confirmed) {
      return
    }
    try {
      await deleteNote(id)
      selectedIdRef.current = null
      setSelected(null)
      setDraft(EMPTY_DRAFT)
      setSaveState('saved')
      void refreshList()
      void refreshDeleted()
    } catch (cause) {
      toast.show(describe(cause), 'bad')
    }
  }

  const refreshDeleted = useCallback(async () => {
    try {
      setDeleted((await listDeletedNotes()).items)
    } catch {
      setDeleted([])
    }
  }, [])

  useEffect(() => {
    void refreshDeleted()
  }, [refreshDeleted])

  async function retryTags() {
    const id = selectedIdRef.current
    if (id === null) {
      return
    }
    try {
      const note = await retagNote(id)
      baseRef.current = note.updated_at
      setSelected(note)
      setDraft({ title: note.title, content: note.content, tags: [...note.tags] })
      setSaveState(dirtyRef.current ? 'dirty' : 'saved')
      void refreshList()
    } catch (cause) {
      toast.show(describe(cause), 'bad')
    }
  }

  async function restore(id: string) {
    try {
      await restoreNote(id)
      void refreshList()
      void refreshDeleted()
    } catch (cause) {
      toast.show(describe(cause), 'bad')
    }
  }

  async function purge(id: string) {
    const confirmed = await confirm.ask({
      title: '彻底删除这条便签？',
      body: '文件会从磁盘上删掉，无法恢复。',
      confirmLabel: '彻底删除',
      danger: true,
    })
    if (!confirmed) {
      return
    }
    try {
      await purgeNote(id)
      void refreshDeleted()
    } catch (cause) {
      toast.show(describe(cause), 'bad')
    }
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
    <div className="pywebview-drag-region setup" onDoubleClick={toggleMaximize}>
      <div className="setup-card is-manager notepad">
        <button
          type="button"
          className="icon-button setup-close"
          aria-label="关闭便签"
          title="关闭"
          onClick={onClose}
        >
          <CloseIcon size={16} />
        </button>
        <div className="notepad-body">
          <div className="notepad-list">
            <div className="notepad-list-actions">
              <div className="notepad-search">
                <SearchIcon size={15} />
                <input
                  value={queryInput}
                  placeholder="搜索标题、标签和内容…"
                  onChange={(event) => setQueryInput(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                      setSearch(queryInput)
                    }
                  }}
                />
              </div>
              <button
                type="button"
                className="button button-primary notepad-new"
                aria-label="新建便签"
                title="新建"
                onClick={startNew}
              >
                <PlusIcon size={16} />
              </button>
            </div>

            {tags.length > 0 && (
              <div className="notepad-tagbar">
                <button
                  type="button"
                  className={`notepad-chip${activeTag === null ? ' is-on' : ''}`}
                  onClick={() => setActiveTag(null)}
                >
                  全部
                </button>
                {tags.map((tag) => (
                  <button
                    key={tag.tag}
                    type="button"
                    className={`notepad-chip${activeTag === tag.tag ? ' is-on' : ''}`}
                    onClick={() => setActiveTag(activeTag === tag.tag ? null : tag.tag)}
                  >
                    {tag.tag}
                    <span className="count">{tag.count}</span>
                  </button>
                ))}
              </div>
            )}

            <div className="notepad-rows">
              {loadError !== null && <div className="form-error">{loadError}</div>}
              {notes !== null && notes.length === 0 && search === '' && activeTag === null && (
                <div className="notepad-empty">
                  <p>还没有便签。</p>
                  <p className="hint">用呼出快捷键，或点「新建」，随手写点什么。</p>
                </div>
              )}
              {notes !== null && notes.length === 0 && (search !== '' || activeTag !== null) && (
                <div className="notepad-empty">没有匹配的便签。</div>
              )}
              {notes?.map((note) => (
                <button
                  key={note.id}
                  type="button"
                  className={`notepad-row${selected?.id === note.id ? ' is-active' : ''}`}
                  onClick={() => void select(note.id)}
                >
                  <span className="notepad-row-title">
                    <span className="notepad-row-label">{note.title || note.excerpt || '（无标题）'}</span>
                    {note.tag_status === 'pending' && <span className="tag-dot is-pending" />}
                    {note.tag_status === 'failed' && <span className="tag-dot is-failed" />}
                  </span>
                  {note.title !== '' && note.excerpt !== '' && (
                    <span className="notepad-row-excerpt">{note.excerpt}</span>
                  )}
                  <span className="notepad-row-meta">
                    {relativeTime(note.updated_at)}
                    {note.tags.length > 0 && <span className="notepad-row-tags">{note.tags.join(' · ')}</span>}
                  </span>
                </button>
              ))}
            </div>

            {deleted !== null && deleted.length > 0 && (
              <div className="notepad-deleted">
                <button
                  type="button"
                  className="notepad-deleted-toggle"
                  onClick={() => setShowDeleted((open) => !open)}
                >
                  回收站（{deleted.length}）{showDeleted ? '▴' : '▾'}
                </button>
                {showDeleted &&
                  deleted.map((note) => (
                    <div key={note.id} className="notepad-deleted-row">
                      <span className="notepad-row-title">
                        <span className="notepad-row-label">{note.title || note.excerpt}</span>
                      </span>
                      <span className="spacer" />
                      <button
                        type="button"
                        className="button button-ghost button-small"
                        onClick={() => void restore(note.id)}
                      >
                        恢复
                      </button>
                      <button
                        type="button"
                        className="icon-button"
                        title="彻底删除"
                        onClick={() => void purge(note.id)}
                      >
                        <TrashIcon size={15} />
                      </button>
                    </div>
                  ))}
              </div>
            )}
          </div>

          <div className="notepad-editor-pane">
            {selected === null && !creating ? (
              <div className="notepad-empty is-pane">
                <p>选择左边的一条，或者新建一条。</p>
              </div>
            ) : (
              <>
                <div className="notepad-editor-head">
                  <input
                    className="notepad-title-input"
                    value={draft.title}
                    placeholder="标题（可选）"
                    onChange={(event) => markDirty({ title: event.target.value })}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') {
                        event.preventDefault()
                      }
                    }}
                  />
                  <div className="notepad-editor-meta">
                    <TagStatus status={selected?.tag_status ?? 'none'} onRetry={() => void retryTags()} />
                    <span className={`notepad-save-state is-${saveState}`}>{saveLabel}</span>
                    {selected !== null && (
                      <button
                        type="button"
                        className="icon-button"
                        title="删除便签"
                        onClick={() => void removeCurrent()}
                      >
                        <TrashIcon size={15} />
                      </button>
                    )}
                  </div>
                  <TagEditor
                    tags={draft.tags}
                    allTags={tags.map((tag) => tag.tag)}
                    onChange={(tags) => markDirty({ tags })}
                  />
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
                <NoteEditor
                  key={editorKey}
                  value={draft.content}
                  onChange={(markdown) => {
                    if (markdown !== draftRef.current.content) {
                      markDirty({ content: markdown })
                    }
                  }}
                  placeholder="写点什么……"
                />
              </>
            )}
          </div>
        </div>
      </div>
      {confirm.dialog}
    </div>
  )
}

function TagStatus({ status, onRetry }: { status: NoteFull['tag_status']; onRetry(): void }) {
  if (status === 'pending') {
    return <span className="notepad-tag-status">AI 打标签中…</span>
  }
  if (status === 'failed') {
    return (
      <span className="notepad-tag-status is-failed">
        打标签失败
        <button type="button" className="button button-ghost button-small" onClick={onRetry}>
          重试
        </button>
      </span>
    )
  }
  return null
}

function TagEditor({
  tags,
  allTags,
  onChange,
}: {
  tags: string[]
  allTags: string[]
  onChange(tags: string[]): void
}) {
  const [input, setInput] = useState('')
  const suggestions = allTags.filter((tag) => !tags.includes(tag) && tag.includes(input.trim()))

  function commit() {
    const tag = input.trim().replace(/^#/, '')
    if (tag !== '' && !tags.includes(tag)) {
      onChange([...tags, tag])
    }
    setInput('')
  }

  return (
    <div className="notepad-tageditor">
      {tags.map((tag) => (
        <span key={tag} className="notepad-chip is-on">
          {tag}
          <button
            type="button"
            aria-label={`移除标签 ${tag}`}
            onClick={() => onChange(tags.filter((entry) => entry !== tag))}
          >
            ×
          </button>
        </span>
      ))}
      <input
        value={input}
        placeholder="+ 标签"
        list="notepad-tag-suggestions"
        onChange={(event) => setInput(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ',') {
            event.preventDefault()
            commit()
          }
          if (event.key === 'Backspace' && input === '' && tags.length > 0) {
            onChange(tags.slice(0, -1))
          }
        }}
        onBlur={commit}
      />
      <datalist id="notepad-tag-suggestions">
        {suggestions.map((tag) => (
          <option key={tag} value={tag} />
        ))}
      </datalist>
    </div>
  )
}

function describe(cause: unknown): string {
  if (cause instanceof ApiError || cause instanceof Error) {
    return cause.message
  }
  return '出了点问题，请再试一次。'
}
