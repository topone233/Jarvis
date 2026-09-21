import { describe, expect, it } from 'vitest'

import { citationRehypePlugin, resolvableMark, tokenizeCitationMarks } from './citationMark'

describe('tokenizeCitationMarks', () => {
  it('splits text around marks', () => {
    expect(tokenizeCitationMarks('结论[1]成立')).toEqual([
      { kind: 'text', value: '结论' },
      { kind: 'citation', number: 1 },
      { kind: 'text', value: '成立' },
    ])
  })

  it('keeps adjacent marks apart', () => {
    expect(tokenizeCitationMarks('[2][10]')).toEqual([
      { kind: 'citation', number: 2 },
      { kind: 'citation', number: 10 },
    ])
  })

  it('ignores bare brackets and three-digit runs', () => {
    expect(tokenizeCitationMarks('[x] 和 [100]')).toEqual([{ kind: 'text', value: '[x] 和 [100]' }])
  })

  it('returns one text token when nothing matches', () => {
    expect(tokenizeCitationMarks('没有引用')).toEqual([{ kind: 'text', value: '没有引用' }])
  })
})

describe('resolvableMark', () => {
  it('accepts exactly the range the citations cover', () => {
    expect(resolvableMark(1, 3)).toBe(true)
    expect(resolvableMark(3, 3)).toBe(true)
    expect(resolvableMark(0, 3)).toBe(false)
    expect(resolvableMark(4, 3)).toBe(false)
  })
})

describe('citationRehypePlugin', () => {
  type Node = { type: string; tagName?: string; value?: string; children?: Node[] }
  const text = (value: string): Node => ({ type: 'text', value })
  const element = (tagName: string, children: Node[]): Node => ({
    type: 'element',
    tagName,
    children,
  })

  it('replaces marks in prose with sup elements', () => {
    const tree = element('p', [text('见[1]')])
    citationRehypePlugin()(tree as never)
    expect(tree.children).toEqual([
      { type: 'text', value: '见' },
      {
        type: 'element',
        tagName: 'sup',
        properties: { dataCitation: 1 },
        children: [{ type: 'text', value: '1' }],
      },
    ])
  })

  it('leaves marks inside code and inline code alone', () => {
    const tree = element('p', [element('code', [text('a[1] = 2')])])
    citationRehypePlugin()(tree as never)
    expect(tree.children?.[0]?.children).toEqual([text('a[1] = 2')])
  })

  it('leaves marks inside fenced blocks alone', () => {
    const tree = element('pre', [element('code', [text('[1]\nputs 1')])])
    citationRehypePlugin()(tree as never)
    expect(tree.children?.[0]?.children?.[0]?.value).toBe('[1]\nputs 1')
  })

  it('walks nested containers', () => {
    const tree = element('blockquote', [element('p', [text('[3]')])])
    citationRehypePlugin()(tree as never)
    expect(tree.children?.[0]?.children?.[0]?.type).toBe('element')
  })
})
