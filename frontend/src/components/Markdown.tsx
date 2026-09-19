/**
 * Markdown rendering for answer text.
 *
 * The plugin arrays are module-level constants on purpose: react-markdown re-runs
 * its whole pipeline when the array identity changes, and a fresh array on every
 * render would rebuild the parse on every frame of a stream.
 */

import {
  memo,
  type ComponentPropsWithoutRef,
  createContext,
  useContext,
} from 'react'
import ReactMarkdown, { type ExtraProps, type Options } from 'react-markdown'
import rehypeHighlight from 'rehype-highlight'
import remarkGfm from 'remark-gfm'

import { citationRehypePlugin } from './citationMark'
import { CodeBlock } from './CodeBlock'
import { codeInfo, type HastNode } from './codeInfo'
import type { Citation } from '../api/types'

/** Exactly what the `*Plugins` props take, without reaching for `unified`. */
type PluginList = NonNullable<Options['rehypePlugins']>

const REMARK_PLUGINS: PluginList = [remarkGfm]
const REHYPE_PLUGINS: PluginList = [
  // `ignoreMissing` keeps a fence labelled with something highlight.js has never
  // heard of from throwing mid-stream, which would blank the answer.
  [rehypeHighlight, { detect: false, ignoreMissing: true, plainText: ['txt', 'text'] }],
  citationRehypePlugin,
]

/**
 * Binds `[n]` marks to the citations of the answer they appear in, and to the
 * panel that opens on click. Provided per answer - a history page renders
 * many answers, each citing its own run's sources.
 */
export interface CitationScopeValue {
  citations: Citation[]
  onOpen(citation: Citation): void
}

export const CitationScope = createContext<CitationScopeValue | null>(null)

type PreProps = ComponentPropsWithoutRef<'pre'> & ExtraProps

/** Hands every fenced block to the toolbar'd renderer, highlighted children included. */
function PreBlock({ node, children }: PreProps) {
  if (node === undefined) {
    return <pre>{children}</pre>
  }
  const { language, code } = codeInfo(node as HastNode)
  return (
    <CodeBlock language={language} code={code}>
      {children}
    </CodeBlock>
  )
}

type HeadingProps = ComponentPropsWithoutRef<'h2'> & ExtraProps

/** Stamps headings for the outline rail to find; the rendering itself is untouched. */
function OutlineHeading({ node, children, ...rest }: HeadingProps) {
  const Tag = (node?.tagName ?? 'h2') as 'h2' | 'h3'
  return (
    <Tag {...rest} data-outline={Tag}>
      {children}
    </Tag>
  )
}

type SupProps = ComponentPropsWithoutRef<'sup'>

/**
 * One `[n]` mark. A number the answer's citations cannot resolve renders as
 * the plain text it would have been - the model occasionally writes `[1]`
 * for its own reasons, and inventing a link for it would be worse than
 * showing it.
 */
function CitationMark(props: SupProps) {
  const scope = useContext(CitationScope)
  const raw = (props as Record<string, unknown>)['data-citation']
  const number_ = typeof raw === 'number' ? raw : Number(raw)
  const citation = scope?.citations[number_ - 1]
  if (scope === null || citation === undefined) {
    return <>{`[${raw ?? ''}]`}</>
  }
  return (
    <sup className="citation-mark" title={citation.title}>
      <button
        type="button"
        onClick={() => scope.onOpen(citation)}
        aria-label={`查看引用 ${number_}：${citation.title}`}
      >
        {number_}
      </button>
    </sup>
  )
}

const COMPONENTS: NonNullable<Options['components']> = {
  pre: PreBlock,
  h2: OutlineHeading,
  h3: OutlineHeading,
  sup: CitationMark,
}

export const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={REMARK_PLUGINS}
        rehypePlugins={REHYPE_PLUGINS}
        components={COMPONENTS}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
})

/** Wraps one answer's Markdown in its own citation scope. */
export function MarkdownWithCitations({
  text,
  citations,
  onOpen,
}: {
  text: string
  citations: Citation[]
  onOpen(citation: Citation): void
}) {
  return (
    <CitationScope.Provider value={{ citations, onOpen }}>
      <Markdown text={text} />
    </CitationScope.Provider>
  )
}
