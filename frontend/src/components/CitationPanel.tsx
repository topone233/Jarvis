/**
 * The right-side panel a citation opens.
 *
 * Two tabs: 引用 holds the excerpt the answer drew from; 文档 holds the whole
 * document, rendered section by section so the cited one can be scrolled to
 * and held highlighted. The panel docks rather than floats: opening it
 * narrows the conversation, it never covers it. The tabs are the same two
 * states the panel has always had - the button that used to move between them
 * is now the tab strip, so the second surface is discoverable, not hidden
 * behind a click.
 */

import { useEffect, useRef, useState } from 'react'

import { fetchKnowledgeContent } from '../api/knowledge'
import type { Citation, KnowledgeContent } from '../api/types'
import { Markdown } from './Markdown'
import { CloseIcon } from './icons'

export function CitationPanel({ citation, onClose }: { citation: Citation; onClose(): void }) {
  // A new citation resets the panel to the excerpt; the document is a
  // deliberate step the user takes again for the next citation.
  const key = citation.chunk_id
  const [tab, setTab] = useState<'cite' | 'doc'>('cite')
  useEffect(() => {
    setTab('cite')
  }, [key])

  return (
    <aside className="right-panel">
      <header className="right-panel-head">
        <nav className="right-panel-tabs" role="tablist" aria-label="引用面板">
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'cite'}
            className={`right-panel-tab${tab === 'cite' ? ' is-active' : ''}`}
            onClick={() => setTab('cite')}
          >
            引用
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'doc'}
            className={`right-panel-tab${tab === 'doc' ? ' is-active' : ''}`}
            onClick={() => setTab('doc')}
          >
            文档
          </button>
        </nav>
        <button type="button" className="icon-button" title="关闭" onClick={onClose}>
          <CloseIcon size={16} />
        </button>
      </header>
      {tab === 'cite' ? (
        <ExcerptView citation={citation} onOpenDocument={() => setTab('doc')} />
      ) : (
        <DocumentView citation={citation} />
      )}
    </aside>
  )
}

function ExcerptView({ citation, onOpenDocument }: { citation: Citation; onOpenDocument(): void }) {
  return (
    <div className="citation-panel-body">
      <div className="citation-panel-heading">
        {citation.number !== undefined && <span className="badge">{citation.number}</span>}
        <span className="citation-panel-title" title={citation.title}>
          {citation.title}
        </span>
      </div>
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
          <div
            key={section.id}
            data-section-id={section.id}
            data-cited={section.id === citedId ? '1' : undefined}
          >
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
