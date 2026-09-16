/**
 * The knowledge base page: the documents Jarvis retrieves from, and the way in.
 *
 * Import accepts one or several files; the backend answers with one outcome per
 * file, and those outcomes are shown exactly as they came - a skipped file says
 * why inline, since what failed is still on screen. Deleting a document moves
 * it to the deleted section below (the backend soft-deletes into the recycle
 * bin), from where it can be restored or dropped for good. The try-search box
 * runs the same search a run would, so the index can be inspected without
 * starting a conversation.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError } from '../api/client'
import {
  KNOWLEDGE_ACCEPT,
  deleteKnowledgeDocument,
  importKnowledge,
  listKnowledgeDocuments,
  searchKnowledge,
  summarizeImportResults,
} from '../api/knowledge'
import { discardTrashItem, listTrash, restoreTrashItem } from '../api/memories'
import type { Citation, KnowledgeDocument, KnowledgeImportResult, TrashItem } from '../api/types'
import { CloseIcon, TrashIcon } from '../components/icons'
import { useConfirm } from '../hooks/useConfirm'
import { useToast } from '../hooks/useToast'

const STATUS_LABELS: Record<string, string> = {
  ready: '就绪',
  ready_without_embeddings: '缺语义索引',
  skipped: '未导入',
}

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status
}

function excerpt(content: string, max = 160): string {
  const single = content.replace(/\s+/g, ' ').trim()
  return single.length > max ? `${single.slice(0, max)}…` : single
}

export function KnowledgePage({ onClose }: { onClose(): void }) {
  const confirm = useConfirm()
  const toast = useToast()
  const fileInput = useRef<HTMLInputElement>(null)
  const [documents, setDocuments] = useState<KnowledgeDocument[] | null>(null)
  const [deleted, setDeleted] = useState<TrashItem[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [importing, setImporting] = useState(false)
  const [results, setResults] = useState<KnowledgeImportResult[] | null>(null)
  const [query, setQuery] = useState('')
  const [searching, setSearching] = useState(false)
  const [searchResults, setSearchResults] = useState<Citation[] | null>(null)

  const reload = useCallback(async () => {
    try {
      const [active, trash] = await Promise.all([listKnowledgeDocuments(), listTrash()])
      setDocuments(active)
      setDeleted(trash.filter((item) => item.entity_type === 'knowledge_document'))
      setLoadError(null)
    } catch (cause) {
      setLoadError(describe(cause))
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  async function upload(fileList: FileList | null) {
    const files = Array.from(fileList ?? [])
    if (files.length === 0) {
      return
    }
    setImporting(true)
    setError(null)
    try {
      const response = await importKnowledge(files)
      setResults(summarizeImportResults(response.items))
      await reload()
    } catch (cause) {
      setError(describe(cause))
    } finally {
      setImporting(false)
      // Cleared so picking the same file again still fires onChange.
      if (fileInput.current !== null) {
        fileInput.current.value = ''
      }
    }
  }

  async function runSearch() {
    const trimmed = query.trim()
    if (trimmed === '') {
      return
    }
    setSearching(true)
    setError(null)
    try {
      const response = await searchKnowledge(trimmed)
      setSearchResults(response.items)
    } catch (cause) {
      setError(describe(cause))
    } finally {
      setSearching(false)
    }
  }

  async function remove(document: KnowledgeDocument) {
    const confirmed = await confirm.ask({
      title: '删除这份文档？',
      body: `「${document.title}」会移到下方的已删除里，之后可以恢复。`,
      confirmLabel: '删除',
      danger: true,
    })
    if (!confirmed) {
      return
    }
    try {
      await deleteKnowledgeDocument(document.id)
      setDocuments((current) => current?.filter((d) => d.id !== document.id) ?? current)
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
    const name = String(item.snapshot.title ?? '')
    const confirmed = await confirm.ask({
      title: '永久删除这份文档？',
      body: `「${name}」及其分块将从数据目录中移除，之后无法恢复。`,
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
          aria-label="关闭知识库"
          title="关闭"
          onClick={onClose}
        >
          <CloseIcon size={16} />
        </button>
        <h1 className="setup-title">知识库</h1>
        <p className="setup-subtitle">
          导入的文档每轮对话都会被检索，命中的段落作为引用带给模型。文件存在本地数据目录里。
        </p>

        {loadError !== null && <div className="form-error">{loadError}</div>}
        {error !== null && <div className="form-error">{error}</div>}

        <div className="knowledge-actions">
          <label className="button button-primary button-small">
            {importing ? '正在导入…' : '上传文档'}
            <input
              ref={fileInput}
              type="file"
              multiple
              accept={KNOWLEDGE_ACCEPT}
              className="knowledge-file-input"
              disabled={importing}
              onChange={(event) => void upload(event.target.files)}
            />
          </label>
          <span className="knowledge-note">
            支持 docx、xlsx、pptx、pdf、markdown、txt 和源码文本；旧版 .doc/.xls/.ppt 请先在 Office
            里另存。扫描版 PDF 没有可提取的文字。
          </span>
        </div>

        {results !== null && results.length > 0 && (
          <ul className="knowledge-imports">
            {results.map((result) => (
              <li key={result.filename} className="knowledge-import">
                <span className={`badge${result.status === 'ready' ? '' : ' badge-quiet'}`}>
                  {statusLabel(result.status)}
                </span>
                <span className="knowledge-import-line">
                  {result.filename}
                  {result.reason !== undefined && (
                    <span className="profile-meta"> · {result.reason}</span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}

        {documents !== null && documents.length === 0 ? (
          <p className="setup-empty">还没有文档。上传后它们会自动分块、建立索引。</p>
        ) : (
          <ul className="profile-list">
            {(documents ?? []).map((document) => (
              <li key={document.id} className="profile-row">
                <div className="profile-head">
                  <span className="profile-name">{document.title}</span>
                  <span className="badge badge-quiet">{statusLabel(document.status)}</span>
                  <span className="badge badge-quiet">{document.chunk_count} 块</span>
                  <span className="spacer" />
                  <button
                    type="button"
                    className="icon-button"
                    title="删除"
                    onClick={() => void remove(document)}
                  >
                    <TrashIcon size={15} />
                  </button>
                </div>
                <span className="profile-meta">
                  {document.original_filename} · 导入于{' '}
                  {new Date(document.created_at).toLocaleDateString()}
                </span>
              </li>
            ))}
          </ul>
        )}

        <div className="knowledge-search">
          <input
            value={query}
            placeholder="输入关键词，试试文档会怎么被检索"
            aria-label="试检索"
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                void runSearch()
              }
            }}
          />
          <button
            type="button"
            className="button button-ghost button-small"
            disabled={searching}
            onClick={() => void runSearch()}
          >
            {searching ? '检索中…' : '试检索'}
          </button>
        </div>

        {searchResults !== null &&
          (searchResults.length === 0 ? (
            <p className="setup-empty">没有命中的段落。</p>
          ) : (
            <ul className="profile-list">
              {searchResults.map((citation) => (
                <li key={citation.chunk_id} className="profile-row">
                  <div className="profile-head">
                    <span className="profile-name">{citation.title}</span>
                    <span className="badge badge-quiet">得分 {citation.score}</span>
                  </div>
                  <span className="profile-meta">{excerpt(citation.content)}</span>
                </li>
              ))}
            </ul>
          ))}

        {deleted !== null && deleted.length > 0 && (
          <details className="memory-deleted">
            <summary>已删除（{deleted.length}）</summary>
            <ul className="profile-list">
              {deleted.map((item) => (
                <li key={item.id} className="profile-row">
                  <div className="profile-head">
                    <span className="profile-name">{String(item.snapshot.title ?? '')}</span>
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
                  <span className="profile-meta">
                    {String(item.snapshot.original_filename ?? '')}
                  </span>
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
