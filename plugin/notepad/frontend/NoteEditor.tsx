/**
 * Typora 风格的所见即所得编辑器：Milkdown Crepe 的一层薄壳。
 *
 * 生命周期刻意简单：实例在挂载时创建、卸载时销毁，value 只在创建那一刻
 * 被读一次。切换笔记、冲突后重载都是父组件换 key 强制重建——比让一个
 * ProseMirror 实例中途换文档要诚实得多，也避开了"外部改了文档、编辑器
 * 里还是旧内容"的同步问题。
 */

import { useEffect, useRef } from 'react'
import { Crepe } from '@milkdown/crepe'
import { editorViewCtx } from '@milkdown/kit/core'
import { InputRule } from '@milkdown/kit/prose/inputrules'
import { $inputRule } from '@milkdown/kit/utils'
// Crepe 的样式是两层：common 是所有组件的结构（block 菜单、链接工具条、
// 拖放指示线的定位），frame 只是调色盘。只导 frame 的话，这些组件会以
// 无样式的块裸排在文档流里。调色盘在 notepad.css 里映射到 Jarvis tokens。
import '@milkdown/crepe/theme/common/style.css'
import '@milkdown/crepe/theme/frame.css'

export interface NoteEditorProps {
  value: string
  onChange(markdown: string): void
  placeholder?: string
}

// GFM 预设的待办输入规则只认「[ ] 」和「[x] 」（括号里必须带空格）；手快
// 打成「[] 」的人只会得到一对方括号，落盘时还被转义成 \[。这条放宽的规则
// 是预设那条的镜像（同样只在列表项里生效、同样删掉匹配文本再改节点），
// 只把匹配放宽到空括号和大写 X。预设的规则先注册，能匹配时轮不到它。
const lenientTaskRule = $inputRule(
  () =>
    new InputRule(/^\[(?<checked>x)?\]\s$/i, (state, match, start, end) => {
      const pos = state.doc.resolve(start)
      let depth = 0
      let node = pos.node(depth)
      while (node && node.type.name !== 'list_item') {
        depth--
        node = pos.node(depth)
      }
      if (!node || node.attrs.checked != null) {
        return null
      }
      const checked = match.groups?.checked !== undefined
      return state.tr
        .deleteRange(start, end)
        .setNodeMarkup(pos.before(depth), undefined, { ...node.attrs, checked })
    }),
)

export function NoteEditor({ value, onChange, placeholder }: NoteEditorProps) {
  const rootRef = useRef<HTMLDivElement>(null)
  // The callback rides a ref so the effect can stay mount-only: the editor's
  // own lifetime is the note's lifetime (the parent keys us by note id).
  const onChangeRef = useRef(onChange)
  onChangeRef.current = onChange

  useEffect(() => {
    const root = rootRef.current
    if (root === null) {
      return
    }
    const crepe = new Crepe({
      root,
      defaultValue: value,
      features: {
        // The AI block button belongs to Crepe's own service, not to Jarvis;
        // a top bar would duplicate the title input right above it.
        [Crepe.Feature.AI]: false,
        [Crepe.Feature.TopBar]: false,
      },
      featureConfigs: {
        // 虚拟光标关掉：它把原生光标藏了、自己画一条几乎看不见的 2px 线
        // （颜色取 --crepe-color-outline）。关掉后 caret-color 回归默认
        // （随文字色），和标题输入框、聊天框是同一支光标。
        [Crepe.Feature.Cursor]: { virtual: false },
        [Crepe.Feature.Placeholder]: { text: placeholder ?? '写点什么……' },
      },
    })
    crepe.editor.use(lenientTaskRule)
    let disposed = false
    crepe.on((listener) => {
      listener.markdownUpdated((_previous, next) => {
        if (!disposed) {
          onChangeRef.current(next)
        }
      })
    })
    void crepe
      .create()
      .then((editor) => {
        // 默认光标落在正文：标题是可选的，落笔的地方才是起点。
        editor.action((ctx) => ctx.get(editorViewCtx).focus())
      })
      .catch((error: unknown) => {
        console.error('编辑器初始化失败', error)
      })
    return () => {
      disposed = true
      void crepe.destroy().catch(() => undefined)
    }
    // `value` is deliberately not a dependency: see the header comment.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return <div className="notepad-editor" ref={rootRef} />
}
