/**
 * The memory page: what Jarvis remembers, what it forgot, and the way back.
 *
 * The list is the active memories - what the model is shown in its `<memory>`
 * section each turn. Deleting one moves it to the deleted section below (the
 * backend soft-deletes into the recycle bin), where it can be restored or
 * dropped for good. Memories the model itself retracted in conversation land
 * in that same section, which is why it exists: without it a "忘掉这条" would
 * be invisible and irreversible.
 */

import { useCallback, useEffect, useState } from 'react'

import {
  deleteMemory,
  discardTrashItem,
  listMemories,
  listTrash,
  restoreTrashItem,
  updateMemory,
} from '../api/memories'
import { ApiError } from '../api/client'
import type { Memory, TrashItem } from '../api/types'
import { CloseIcon, TrashIcon } from '../components/icons'
import { useConfirm } from '../hooks/useConfirm'
import { useToast } from '../hooks/useToast'

const KIND_LABELS: Record<string, string> = {
  profile: '个人信息',
  preference: '偏好',
  fact: '事实',
  decision: '决定',
}

function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind
}

export function MemoryPage({ onClose }: { onClose(): void }) {
  const confirm = useConfirm()
  const toast = useToast()
  const [memories, setMemories] = useState<Memory[] | null>(null)
  const [deleted, setDeleted] = useState<TrashItem[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [draft, setDraft] = useState({ key: '', content: '' })

  const reload = useCallback(async () => {
    try {
      const [active, trash] = await Promise.all([listMemories(), listTrash()])
      setMemories(active)
      setDeleted(trash.filter((item) => item.entity_type === 'memory'))
      setLoadError(null)
    } catch (cause) {
      setLoadError(describe(cause))
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  function startEdit(memory: Memory) {
    setEditingId(memory.id)
    setDraft({ key: memory.memory_key, content: memory.content })
    setError(null)
  }

  async function save(memory: Memory) {
    const key = draft.key.trim()
    const content = draft.content.trim()
    if (key === '' || content === '') {
      setError('键和内容都不能为空。')
      return
    }
    try {
      const saved = await updateMemory(memory.id, { memory_key: key, content })
      setMemories((current) => current?.map((m) => (m.id === saved.id ? saved : m)) ?? current)
      setEditingId(null)
      setError(null)
    } catch (cause) {
      setError(describe(cause))
    }
  }

  async function remove(memory: Memory) {
    const confirmed = await confirm.ask({
      title: '删除这条记忆？',
      body: `「${memory.memory_key}」会移到下方的已删除里，之后可以恢复。`,
      confirmLabel: '删除',
      danger: true,
    })
    if (!confirmed) {
      return
    }
    try {
      await deleteMemory(memory.id)
      setMemories((current) => current?.filter((m) => m.id !== memory.id) ?? current)
      await reload()
      // The row is gone from the list and the deleted section stays folded:
      // without a word it would not be clear where it went.
      toast.show('已删除，可在下方「已删除」中恢复')
    } catch (cause) {
      setError(describe(cause))
    }
  }

  async function restore(item: TrashItem) {
    try {
      await restoreTrashItem(item.id)
      await reload()
      setError(null)
    } catch (cause) {
      setError(describe(cause))
    }
  }

  async function discard(item: TrashItem) {
    const name = String(item.snapshot.memory_key ?? '')
    const confirmed = await confirm.ask({
      title: '永久删除这条记忆？',
      body: `「${name}」将从数据目录中移除，之后无法恢复。`,
      confirmLabel: '永久删除',
      danger: true,
    })
    if (!confirmed) {
      return
    }
    try {
      await discardTrashItem(item.id)
      setDeleted((current) => current?.filter((entry) => entry.id !== item.id) ?? current)
      // The row is gone and nothing on screen says where; this is the one
      // destruction a toast has to report.
      toast.show('已永久删除')
    } catch (cause) {
      setError(describe(cause))
    }
  }

  return (
    <div className="setup">
      <div className="setup-card is-manager">
        <button
          type="button"
          className="icon-button setup-close"
          aria-label="关闭记忆"
          title="关闭"
          onClick={onClose}
        >
          <CloseIcon size={16} />
        </button>
        <h1 className="setup-title">记忆</h1>
        <p className="setup-subtitle">
          Jarvis 在对话里记住的跨会话信息，每轮回答都会带给模型。可以改、可以删。
        </p>

        {loadError !== null && <div className="form-error">{loadError}</div>}
        {error !== null && <div className="form-error">{error}</div>}

        {memories !== null && memories.length === 0 ? (
          <p className="setup-empty">还没有记忆。对话里明确说出的事实、偏好和决定会被自动记住。</p>
        ) : (
          <ul className="profile-list">
            {(memories ?? []).map((memory) =>
              editingId === memory.id ? (
                <li key={memory.id} className="profile-row is-editing">
                  <div className="field-row">
                    <div className="field">
                      <label htmlFor={`memory-key-${memory.id}`}>键</label>
                      <input
                        id={`memory-key-${memory.id}`}
                        value={draft.key}
                        onChange={(event) => setDraft({ ...draft, key: event.target.value })}
                      />
                    </div>
                    <div className="field">
                      <label>类型</label>
                      <input value={kindLabel(memory.kind)} disabled />
                    </div>
                  </div>
                  <div className="field">
                    <label htmlFor={`memory-content-${memory.id}`}>内容</label>
                    <textarea
                      id={`memory-content-${memory.id}`}
                      value={draft.content}
                      onChange={(event) => setDraft({ ...draft, content: event.target.value })}
                    />
                  </div>
                  <div className="profile-actions">
                    <button
                      type="button"
                      className="button button-primary button-small"
                      onClick={() => void save(memory)}
                    >
                      保存
                    </button>
                    <button
                      type="button"
                      className="button button-ghost button-small"
                      onClick={() => setEditingId(null)}
                    >
                      取消
                    </button>
                  </div>
                </li>
              ) : (
                <li key={memory.id} className="profile-row">
                  <div className="profile-head">
                    <span className="badge">{kindLabel(memory.kind)}</span>
                    <span className="profile-name">{memory.memory_key}</span>
                    <span className="badge badge-quiet">
                      {memory.scope === 'global' ? '全局' : '项目'}
                    </span>
                    {memory.confirmation_count > 1 && (
                      <span className="badge badge-quiet">确认 {memory.confirmation_count} 次</span>
                    )}
                    <span className="spacer" />
                    <button
                      type="button"
                      className="button button-ghost button-small"
                      onClick={() => startEdit(memory)}
                    >
                      编辑
                    </button>
                    <button
                      type="button"
                      className="icon-button"
                      title="删除"
                      onClick={() => void remove(memory)}
                    >
                      <TrashIcon size={15} />
                    </button>
                  </div>
                  <span className="profile-meta">{memory.content}</span>
                  {memory.source_excerpt !== '' && (
                    <span className="profile-meta">来自：{memory.source_excerpt}</span>
                  )}
                </li>
              ),
            )}
          </ul>
        )}

        {deleted !== null && deleted.length > 0 && (
          <details className="memory-deleted">
            <summary>已删除（{deleted.length}）</summary>
            <ul className="profile-list">
              {deleted.map((item) => (
                <li key={item.id} className="profile-row">
                  <div className="profile-head">
                    <span className="badge badge-quiet">
                      {kindLabel(String(item.snapshot.kind ?? ''))}
                    </span>
                    <span className="profile-name">{String(item.snapshot.memory_key ?? '')}</span>
                    <span className="spacer" />
                    <button
                      type="button"
                      className="button button-ghost button-small"
                      onClick={() => void restore(item)}
                    >
                      恢复
                    </button>
                    <button
                      type="button"
                      className="button button-danger button-small"
                      onClick={() => void discard(item)}
                    >
                      永久删除
                    </button>
                  </div>
                  <span className="profile-meta">{String(item.snapshot.content ?? '')}</span>
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
      {confirm.dialog}
    </div>
  )
}

function describe(cause: unknown): string {
  if (cause instanceof ApiError || cause instanceof Error) {
    return cause.message
  }
  return '出了点问题，请再试一次。'
}
