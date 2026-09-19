/**
 * The `[1]` marks an answer uses to cite the knowledge it drew from.
 *
 * The tokenizer is a pure function so the rules - what counts as a mark,
 * what does not - test without a DOM. The rehype plugin below applies it to
 * hast text nodes; code and inline code are left alone, because a `[1]` in
 * a snippet is the snippet's own text, not a citation.
 */

export type CitationToken = { kind: 'text'; value: string } | { kind: 'citation'; number: number }

const MARK_RE = /\[(\d{1,2})\]/g

export function tokenizeCitationMarks(text: string): CitationToken[] {
  const tokens: CitationToken[] = []
  let cursor = 0
  for (const match of text.matchAll(MARK_RE)) {
    const index = match.index ?? 0
    if (index > cursor) {
      tokens.push({ kind: 'text', value: text.slice(cursor, index) })
    }
    tokens.push({ kind: 'citation', number: Number(match[1]) })
    cursor = index + match[0].length
  }
  if (cursor < text.length) {
    tokens.push({ kind: 'text', value: text.slice(cursor) })
  }
  return tokens
}

/** Whether a mark should render as a link: it must name a known citation. */
export function resolvableMark(number_: number, count: number): boolean {
  return number_ >= 1 && number_ <= count
}

type HastText = { type: 'text'; value: string }
type HastNode = {
  type: string
  tagName?: string
  value?: string
  properties?: Record<string, unknown>
  children?: HastNode[]
}

/** Tags whose text is code, not prose - a `[1]` there belongs to the code. */
const CODE_TAGS = new Set(['code', 'pre'])

function isTextNode(node: HastNode): node is HastText & HastNode {
  return node.type === 'text'
}

/**
 * Replaces every resolvable-shaped `[n]` in prose text with a `sup` element
 * carrying `data-citation`. Whether the number actually names a citation is
 * decided at render time, in the component - the plugin stays a pure
 * syntactic pass so it can sit in the module-level plugin list (a fresh
 * plugin array would rebuild the parse on every frame of a stream).
 */
export function citationRehypePlugin() {
  return (tree: HastNode) => {
    visit(tree)
  }

  function visit(node: HastNode, inCode = false): void {
    const children = node.children
    if (children === undefined) {
      return
    }
    const code = inCode || (node.tagName !== undefined && CODE_TAGS.has(node.tagName))
    const next: HastNode[] = []
    for (const child of children) {
      if (!code && isTextNode(child) && /\[\d{1,2}\]/.test(child.value)) {
        next.push(...replaceText(child))
      } else {
        visit(child, code)
        next.push(child)
      }
    }
    node.children = next
  }

  function replaceText(node: HastText): HastNode[] {
    return tokenizeCitationMarks(node.value).map((token) => {
      if (token.kind === 'text') {
        return { type: 'text', value: token.value }
      }
      return {
        type: 'element',
        tagName: 'sup',
        properties: { dataCitation: token.number },
        children: [{ type: 'text', value: String(token.number) }],
      }
    })
  }
}
