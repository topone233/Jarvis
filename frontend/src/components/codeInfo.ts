/**
 * The logic behind rendered code blocks, kept apart from the component so the
 * fast-refresh boundary stays clean and the parsing tests without a DOM.
 *
 * Everything the toolbar needs - the raw source, the language - comes from the
 * hast node react-markdown hands over, not from the rendered children, which
 * rehype-highlight has already turned into spans.
 */

/** The subset of hast this module walks. Structural, so it tests without a DOM. */
export interface HastNode {
  type: string
  value?: string
  tagName?: string
  properties?: { className?: unknown }
  children?: HastNode[]
}

export interface CodeInfo {
  language: string
  code: string
}

/** Every text leaf of a hast subtree, in document order. */
export function hastText(node: HastNode): string {
  if (node.type === 'text') {
    return node.value ?? ''
  }
  return (node.children ?? []).map(hastText).join('')
}

/**
 * The fenced block behind a `pre`: the language from a `language-xxx` class on
 * the inner `code`, the raw source from the whole subtree.
 */
export function codeInfo(pre: HastNode): CodeInfo {
  const code = pre.children?.find((child) => child.tagName === 'code')
  const raw = code?.properties?.className
  const classes = typeof raw === 'string' ? raw.split(/\s+/) : Array.isArray(raw) ? raw : []
  const marker = classes.find((name) => typeof name === 'string' && name.startsWith('language-'))
  return {
    language: typeof marker === 'string' ? marker.slice('language-'.length) : '',
    code: hastText(code ?? pre),
  }
}

export type PreviewKind = 'mermaid' | 'svg' | 'html'

/** The three languages that render instead of just showing their source. */
export function previewKind(language: string): PreviewKind | null {
  switch (language.trim().toLowerCase()) {
    case 'mermaid':
      return 'mermaid'
    case 'svg':
      return 'svg'
    case 'html':
      return 'html'
    default:
      return null
  }
}

/**
 * An SVG as a data URL for an `<img>`. Inside an img, scripts in the SVG never
 * run - that is what makes this safe without pulling in a sanitizer.
 */
export function svgDataUrl(code: string): string {
  return `data:image/svg+xml;utf8,${encodeURIComponent(code)}`
}
