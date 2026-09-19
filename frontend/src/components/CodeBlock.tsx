/**
 * A fenced code block with a toolbar that surfaces itself on hover.
 *
 * Three languages render instead of just displaying their source: mermaid
 * diagrams, SVG and HTML. The first two are declarative - they open as a
 * preview immediately. HTML is not: it runs on an explicit click, inside a
 * sandboxed iframe with no same-origin, so a demo can execute but never reach
 * this page's storage or API.
 */

import { useEffect, useRef, useState, type ReactNode } from 'react'

import { useCopy } from '../hooks/useCopy'
import { CheckIcon, CodeIcon, CopyIcon, EyeIcon, PlayIcon, RefreshIcon } from './icons'
import { previewKind, svgDataUrl } from './codeInfo'

/** How long a stream may pause before a half-written mermaid block is tried. */
const MERMAID_DEBOUNCE = 500

let mermaidPromise: Promise<(typeof import('mermaid'))['default']> | null = null

function mermaidModule() {
  // Loaded once, on demand: mermaid is over a megabyte and almost every
  // conversation never asks for it. Code splitting keeps it out of the bundle
  // until the first mermaid fence actually arrives.
  mermaidPromise ??= import('mermaid').then((module) => {
    module.default.initialize({ startOnLoad: false })
    return module.default
  })
  return mermaidPromise
}

export function CodeBlock({
  language,
  code,
  children,
}: {
  language: string
  code: string
  children: ReactNode
}) {
  const kind = previewKind(language)
  // HTML waits for an explicit run; mermaid and svg render on open.
  const [view, setView] = useState<'preview' | 'source'>(kind === 'html' ? 'source' : 'preview')
  const [copied, copy] = useCopy()
  const [mermaidSvg, setMermaidSvg] = useState<string | null>(null)
  // Bumping this remounts the iframe, which restarts its scripts.
  const [runCount, setRunCount] = useState(0)
  const runId = useRef(0)

  // A diagram is tried only after the source has sat still for a moment. While
  // the answer is streaming, the fence keeps growing and every change resets
  // the timer; an unclosed fence fails to parse anyway, and a failed parse
  // falls back to the source rather than flashing an error that would be gone
  // on the next token.
  useEffect(() => {
    if (kind !== 'mermaid' || view !== 'preview') {
      return
    }
    let dropped = false
    setMermaidSvg(null)
    const timer = window.setTimeout(() => {
      const id = `mermaid-${(runId.current += 1)}`
      mermaidModule()
        .then((mermaid) => mermaid.render(id, code))
        .then((rendered) => {
          if (!dropped) {
            setMermaidSvg(rendered.svg)
          }
        })
        .catch(() => {
          if (!dropped) {
            setView('source')
          }
        })
    }, MERMAID_DEBOUNCE)
    return () => {
      dropped = true
      window.clearTimeout(timer)
    }
  }, [kind, view, code])

  const hasRun = runCount > 0
  const showPreview = view === 'preview' && kind !== null && (kind !== 'html' || hasRun)
  // An HTML block that has never run has no preview to switch to yet.
  const canToggle = kind !== null && (kind !== 'html' || hasRun)

  return (
    <div className="code-block">
      <div className="code-toolbar">
        <span className="code-lang">{language || 'text'}</span>
        {kind === 'html' && (
          <button
            type="button"
            className="icon-button"
            title={showPreview ? '重新运行' : '运行'}
            onClick={() => {
              setRunCount((current) => current + 1)
              setView('preview')
            }}
          >
            {showPreview ? <RefreshIcon size={15} /> : <PlayIcon size={15} />}
          </button>
        )}
        {canToggle && (
          <button
            type="button"
            className="icon-button"
            title={showPreview ? '查看源码' : '查看预览'}
            onClick={() => setView(showPreview ? 'source' : 'preview')}
          >
            {showPreview ? <CodeIcon size={15} /> : <EyeIcon size={15} />}
          </button>
        )}
        <button type="button" className="icon-button" title="复制" onClick={() => copy(code)}>
          {copied ? <CheckIcon size={15} /> : <CopyIcon size={15} />}
        </button>
      </div>

      {showPreview ? (
        kind === 'mermaid' ? (
          mermaidSvg !== null && (
            <div className="code-preview" dangerouslySetInnerHTML={{ __html: mermaidSvg }} />
          )
        ) : kind === 'svg' ? (
          <div className="code-preview">
            <img className="code-svg" src={svgDataUrl(code)} alt="SVG 预览" />
          </div>
        ) : (
          <iframe
            key={runCount}
            className="code-frame"
            sandbox="allow-scripts"
            srcDoc={code}
            title="HTML 预览"
          />
        )
      ) : (
        <pre>{children}</pre>
      )}
    </div>
  )
}
