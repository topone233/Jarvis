/**
 * The right-side drawer a citation opens.
 *
 * Two states, one column: the excerpt the answer drew from by default, and -
 * on 查看原文 - the whole document, rendered section by section so the cited
 * one can be scrolled to and held highlighted. The drawer docks rather than
 * floats: opening it narrows the conversation, it never covers it.
 */

import { useEffect, useRef, useState } from 'react'

import { fetchKnowledgeContent } from '../api/knowledge'
import type { Citation, KnowledgeContent } from '../api/types'
import { Markdown } from './Markdown'
import { CloseIcon } from './icons'

export function CitationPanel({
  citation,
  onClose,
}: {
  citation: Citation
  onClose(): void
}) {
  // A new citation resets the panel to the excerpt; the full document is a
  // deliberate step the user takes again for the next citation.
  const key = citation.chunk_id
  const [mode, setMode] = useState<'excerpt' | 'document'>('excerpt')
  useEffect(() => {
    setMode('excerpt')
  }, [key])

  return (
    <aside className="citation-panel">
      <header className="citation-panel-header">
        <div className="citation-panel-heading">
          {citation.number !== undefined && <span className="badge">{citation.number}</span>}
          <span className="citation-panel-title" title={citation.title}>
            {citation.title}
          </span>
        </div>
        <button type="button" className="icon-button" title="关闭" onClick={onClose}>
          <CloseIcon size={16} />
        </button>
      </header>
      {mode === 'excerpt' ? (
        <ExcerptView citation={citation} onOpenDocument={() => setMode('document')} />
      ) : (
        <DocumentView citation={citation} />
      )}
    </aside>
  )
}

function ExcerptView({
  citation,
  onOpenDocument,
}: {
  citation: Citation
  onOpenDocument(): void
}) {
  return (
    <div className="citation-panel-body">
      {citation.section_title !== null && citation.section_title !== undefined && (
        <p className="citation-panel-section">{citation.section_title}</p>
      )}
      <div className="citation-panel-excerpt">{citation.content}</div>
      <div className="citation-panel-actions">
        <button type="button" className="button button-ghost" onClick={onOpenDocument}>
          查看原文
        </button>
      </div>
    </div>
  )
}

/**
 * The whole document, one block per section. Slicing by the backend's own
 * offsets is what makes the sections addressable: each block carries its id,
 * so both the initial scroll and the TOC land on real elements.
 */
function DocumentView({ citation }: { citation: Citation }) {
  const [document, setDocument] = useState<KnowledgeContent | null>(null)
  const [error, setError] = useState<string | null>(null)
  const bodyRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    let dropped = false
    setError(null)
    setDocument(null)
    fetchKnowledgeContent(citation.document_id)
      .then((content) => {
        if (!dropped) {
          setDocument(content)
        }
      })
      .catch(() => {
        if (!dropped) {
          setError('原文读取失败，文档可能已被删除。')
        }
      })
    return () => {
      dropped = true
    }
  }, [citation.document_id])

  const citedId = citation.section_id ?? ''
  useEffect(() => {
    if (document === null) {
      return
    }
    const target = bodyRef.current?.querySelector(`[data-section-id="${citedId}"]`)
    target?.scrollIntoView({ block: 'start' })
  }, [document, citedId])

  const scrollTo = (sectionId: string) => {
    bodyRef.current
      ?.querySelector(`[data-section-id="${CSS.escape(sectionId)}"]`)
      ?.scrollIntoView({ block: 'start' })
  }

  if (error !== null) {
    return <div className="citation-panel-body">{error}</div>
  }
  if (document === null) {
    return <div className="citation-panel-body">正在读取原文…</div>
  }
  return (
    <>
      <DocumentToc document={document} citedId={citedId} onJump={scrollTo} />
      <div className="citation-panel-body" ref={bodyRef}>
        {document.sections.map((section) => (
          <div key={section.id} data-section-id={section.id} data-cited={section.id === citedId ? '1' : undefined}>
            {section.id === citedId && <div className="citation-panel-marker">引用来源</div>}
            <Markdown text={document.content.slice(section.start, section.end)} />
          </div>
        ))}
      </div>
    </>
  )
}

function DocumentToc({
  document,
  citedId,
  onJump,
}: {
  document: KnowledgeContent
  citedId: string
  onJump(sectionId: string): void
}) {
  return (
    <nav className="citation-panel-toc">
      {document.sections.map((section) => (
        <button
          key={section.id}
          type="button"
          className={`citation-toc-item${section.id === citedId ? ' is-cited' : ''}`}
          onClick={() => onJump(section.id)}
        >
          {section.title || section.id}
        </button>
      ))}
    </nav>
  )
}
