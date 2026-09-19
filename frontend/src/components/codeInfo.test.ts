import { describe, expect, it } from 'vitest'

import { codeInfo, hastText, previewKind, svgDataUrl, type HastNode } from './codeInfo'

/** A hast `pre` the way rehype-highlight leaves it: one `code` child of spans. */
function pre(language: string, spans: HastNode[]): HastNode {
  return {
    type: 'element',
    tagName: 'pre',
    children: [
      {
        type: 'element',
        tagName: 'code',
        properties: { className: language === '' ? [] : [`language-${language}`, 'hljs'] },
        children: spans,
      },
    ],
  }
}

const text = (value: string): HastNode => ({ type: 'text', value })

describe('codeInfo', () => {
  it('reads the language and the raw source out of highlighted spans', () => {
    const info = codeInfo(pre('ts', [text('const '), text('x'), text(' = 1')]))
    expect(info).toEqual({ language: 'ts', code: 'const x = 1' })
  })

  it('keeps newlines, so multi-line source survives the walk', () => {
    const info = codeInfo(pre('mermaid', [text('flowchart TD\n  A --> B')]))
    expect(info).toEqual({ language: 'mermaid', code: 'flowchart TD\n  A --> B' })
  })

  it('falls back to the pre itself when there is no inner code', () => {
    const info = codeInfo({ type: 'element', tagName: 'pre', children: [text('plain')] })
    expect(info).toEqual({ language: '', code: 'plain' })
  })

  it('reads a plain string className too', () => {
    const node: HastNode = {
      type: 'element',
      tagName: 'code',
      properties: { className: 'language-js hljs' },
      children: [text('1')],
    }
    expect(codeInfo({ type: 'element', tagName: 'pre', children: [node] })).toEqual({
      language: 'js',
      code: '1',
    })
  })
})

describe('previewKind', () => {
  it('maps the three renderable languages', () => {
    expect(previewKind('mermaid')).toBe('mermaid')
    expect(previewKind('svg')).toBe('svg')
    expect(previewKind('html')).toBe('html')
  })

  it('is case-insensitive and tolerates padding', () => {
    expect(previewKind(' Mermaid ')).toBe('mermaid')
    expect(previewKind('HTML')).toBe('html')
  })

  it('leaves everything else as plain code', () => {
    expect(previewKind('python')).toBeNull()
    expect(previewKind('')).toBeNull()
  })
})

describe('hastText', () => {
  it('joins nested leaves in document order', () => {
    const node: HastNode = {
      type: 'element',
      children: [{ type: 'element', children: [text('a'), text('b')] }, text('c')],
    }
    expect(hastText(node)).toBe('abc')
  })
})

describe('svgDataUrl', () => {
  it('encodes the source so it survives as one URL', () => {
    const url = svgDataUrl('<svg><text>a & b</text></svg>')
    expect(url.startsWith('data:image/svg+xml;utf8,%3Csvg%3E')).toBe(true)
    expect(url).toContain('%26')
  })
})
