/**
 * 桌面壳弹窗窗口的页面：/popup/<pluginId>。
 *
 * 唤出的独立小窗直接加载这个路径，没有外壳、没有侧栏，只渲染目标插件的
 * quickCapture 组件。窗口的显示/隐藏由 Python 侧管理；页面负责的一件事
 * 是"内容没了就收窗"：quickCapture 关闭时返回 null，挂载点随之变空，
 * MutationObserver 看到这一刻就调 Python 的 hide_popup——Esc、✕、保存后
 * 自动收起走的是同一条路，插件代码依旧不用知道桌面壳的存在。浏览器里
 * window.pywebview 不存在，hide_popup 是空操作，这个路径照样能用（无壳
 * 时的降级验证入口）。
 */

import { useEffect, useRef } from 'react'

import { usePluginFrontends } from './registry'

export function PopupWindow({ pluginId }: { pluginId: string }) {
  const { frontends, ready } = usePluginFrontends()
  const QuickCapture = ready
    ? frontends.find((entry) => entry.id === pluginId)?.quickCapture
    : undefined
  const holderRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const holder = holderRef.current
    if (holder === null) {
      return
    }
    const observer = new MutationObserver(() => {
      if (!holder.hasChildNodes()) {
        void window.pywebview?.api.hide_popup(pluginId)
      }
    })
    observer.observe(holder, { childList: true })
    return () => observer.disconnect()
  }, [pluginId])

  return (
    <div ref={holderRef} className="popup-holder">
      {QuickCapture === undefined ? null : <QuickCapture />}
    </div>
  )
}
