/**
 * 无边框主窗口的窗口控制：右上角悬浮的 — □ ✕ 三键，没有条。
 *
 * 标题条整条移除后（用户：不需要 logo，不需要顶部一长条），窗口控制的
 * 形状仍是操作系统的方形/hover/关闭变红；页面内容顶到窗口上缘。拖拽不
 * 在这里——改挂在页面空白容器上（Sidebar/ChatPage 等的
 * `pywebview-drag-region`，direct-target-only，子元素照常点击）。
 * 浏览器里 window.pywebview 不存在，整个组件不渲染。
 */

import { useEffect, useState } from 'react'

import { isShell, SHELL_MAXIMIZED_EVENT } from '../plugins/shell'
import { CloseIcon, MaximizeIcon, MinusIcon, WindowRestoreIcon } from './icons'

export function WindowControls() {
  // □ 还是 ❐ 跟随窗口真实状态：Python 在最大化/还原事件里广播，见
  // __main__.py 的 Shell。
  const [maximized, setMaximized] = useState(false)

  useEffect(() => {
    function sync(event: Event) {
      setMaximized((event as CustomEvent<boolean>).detail)
    }
    window.addEventListener(SHELL_MAXIMIZED_EVENT, sync)
    return () => window.removeEventListener(SHELL_MAXIMIZED_EVENT, sync)
  }, [])

  // CSS 需要知道壳态（比如 setup 页自己的关闭按钮要让出右上角）。
  useEffect(() => {
    document.documentElement.classList.toggle('is-shell', isShell())
    return () => document.documentElement.classList.remove('is-shell')
  }, [])

  if (!isShell()) {
    return null
  }

  const api = window.pywebview?.api

  return (
    <div className="window-controls">
      <button
        type="button"
        className="window-button"
        title="最小化"
        onClick={() => api?.minimize_main()}
      >
        <MinusIcon size={15} />
      </button>
      <button
        type="button"
        className="window-button"
        title={maximized ? '还原' : '最大化'}
        onClick={() => api?.toggle_maximize_main()}
      >
        {maximized ? <WindowRestoreIcon size={14} /> : <MaximizeIcon size={13} />}
      </button>
      <button
        type="button"
        className="window-button is-close"
        title="隐藏到后台"
        onClick={() => api?.hide_main()}
      >
        <CloseIcon size={14} />
      </button>
    </div>
  )
}
