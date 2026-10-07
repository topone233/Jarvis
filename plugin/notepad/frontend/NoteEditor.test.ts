import { describe, expect, it } from 'vitest'

import { guardMdastImageStrings, type MdastImageNode } from './NoteEditor'

// crepe 的 remark-image-block 会把段落里唯一的图改写成 image-block，而 remark
// 给无标题图片的 title、无 alt 图片的 alt 都是 null；image-block（caption）与
// image（title/alt）的 prosemirror attr 又都是 validate: 'string'——null 一进来
// 校验就抛 RangeError，整张图被 transformer 静默吞掉。这个守卫把 null 补成
// 空串，覆盖两类节点。

function parse(value: unknown): MdastImageNode {
  return value as MdastImageNode
}

describe('guardMdastImageStrings', () => {
  it('补齐无标题 image-block 的 title（本应用落盘图片的标准形态）', () => {
    const tree = parse({
      type: 'root',
      children: [{ type: 'image-block', url: '/a.png', alt: '1.00', title: null }],
    })
    guardMdastImageStrings(tree)
    expect(tree.children?.[0].title).toBe('')
    expect(tree.children?.[0].url).toBe('/a.png')
  })

  it('补齐无标题行内 image 的 title 与无 alt 的 alt', () => {
    const tree = parse({
      type: 'root',
      children: [
        {
          type: 'paragraph',
          children: [
            { type: 'text', value: '前文 ' },
            { type: 'image', url: '/b.png', alt: null, title: null },
          ],
        },
      ],
    })
    guardMdastImageStrings(tree)
    const image = tree.children?.[0].children?.[1]
    expect(image?.title).toBe('')
    expect(image?.alt).toBe('')
  })

  it('不碰已有标题与其他节点', () => {
    const tree = parse({
      type: 'root',
      children: [
        { type: 'image-block', url: '/c.png', alt: '1.00', title: '说明' },
        { type: 'paragraph', children: [{ type: 'text', value: '普通文本' }] },
      ],
    })
    guardMdastImageStrings(tree)
    expect(tree.children?.[0].title).toBe('说明')
    expect(tree.children?.[1].children?.[0].value).toBe('普通文本')
  })

  it('空串与 undefined 同样命中守卫（mdast 的 title 可能缺失或为 null）', () => {
    const tree = parse({
      type: 'root',
      children: [{ type: 'image', url: '/d.png', title: undefined }],
    })
    guardMdastImageStrings(tree)
    expect(tree.children?.[0].title).toBe('')
    expect(tree.children?.[0].alt).toBe('')
  })
})
