/**
 * Markdown rendering for answer text.
 *
 * The plugin arrays are module-level constants on purpose: react-markdown re-runs
 * its whole pipeline when the array identity changes, and a fresh array on every
 * render would rebuild the parse on every frame of a stream.
 */

import { memo } from 'react'
import ReactMarkdown, { type Options } from 'react-markdown'
import rehypeHighlight from 'rehype-highlight'
import remarkGfm from 'remark-gfm'

/** Exactly what the `*Plugins` props take, without reaching for `unified`. */
type PluginList = NonNullable<Options['rehypePlugins']>

const REMARK_PLUGINS: PluginList = [remarkGfm]
const REHYPE_PLUGINS: PluginList = [
  // `ignoreMissing` keeps a fence labelled with something highlight.js has never
  // heard of from throwing mid-stream, which would blank the answer.
  [rehypeHighlight, { detect: false, ignoreMissing: true, plainText: ['txt', 'text'] }],
]

export const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown remarkPlugins={REMARK_PLUGINS} rehypePlugins={REHYPE_PLUGINS}>
        {text}
      </ReactMarkdown>
    </div>
  )
})
