/**
 * 桌面壳（backend/app/desktop，pywebview）的前端半桥。
 *
 * Python 侧只发 pluginId：这里负责把 id 翻译成该插件配置的组合键，合成一
 * 个 keydown 派发到 window——插件弹窗自己的监听（hotkeyMatches）照常命中，
 * 插件代码因此一行不改，热键语义也仍然只在 hotkey.ts 一处。
 *
 * 反向通道同理：设置页改了热键或开关插件，registry 派发
 * PLUGINS_CHANGED_EVENT，这里转手调 Python 的 refresh_hotkeys 重新注册
 * 全局键。浏览器里 window.pywebview 不存在，这一切都是空操作，dev 流程
 * 不受影响。
 */

import { useCallback, useEffect, useState } from 'react'

import { listPlugins } from '../api/plugins'
import { parseHotkey, syntheticInit } from './hotkey'
import { PLUGINS_CHANGED_EVENT } from './registry'

/** pywebview 注入的 JS API（只在桌面壳里存在），只声明我们用到的部分。 */
interface PywebviewApi {
  api: {
    refresh_hotkeys(): unknown
    hide_popup(pluginId: string): unknown
    /** 弹窗页签行图钉（usePopupPin）→ Shell 的持久状态，两端各写一半。 */
    get_popup_pin(pluginId: string): boolean | Promise<boolean>
    set_popup_pin(pluginId: string, pinned: boolean): unknown
    /** 窗口控制按钮（WindowControls.tsx）→ Python Shell 的同名方法，两端各写一半。 */
    minimize_main(): unknown
    toggle_maximize_main(): unknown
    /** ✕ = 隐藏到托盘，不退出；真退出在托盘菜单。 */
    hide_main(): unknown
  }
}

declare global {
  interface Window {
    pywebview?: PywebviewApi
    __jarvisShell?: {
      summonPlugin(pluginId: string): void
      navigate(path: string): void
    }
  }
}

/** 主窗口里的 SPA 跳转请求（桌面壳唤出整页插件时用）。 */
export const SHELL_NAVIGATE_EVENT = 'jarvis-shell-navigate'
/** Python 往主窗口发的提示（热键冲突之类），Shell 监听后走 toast。 */
export const SHELL_NOTICE_EVENT = 'jarvis-shell-notice'
/** 主窗口最大化状态变化（Python 广播 true/false），标题条据此换 □/❐。 */
export const SHELL_MAXIMIZED_EVENT = 'jarvis-shell-maximized'

export function isShell(): boolean {
  return typeof window !== 'undefined' && window.pywebview !== undefined
}

/** 双击页面空白拖拽区 = 最大化/还原（无边框窗口的标题条没有了，约定跟着
 *  拖拽区走）。浏览器里是空操作。 */
export function toggleMaximize(): void {
  if (isShell()) {
    void window.pywebview?.api.toggle_maximize_main()
  }
}

/** pywebview 迟于页面脚本注入；用的时候在就用，不在就放弃这一次。 */
function shellApi(): PywebviewApi['api'] | null {
  return window.pywebview?.api ?? null
}

/** 把一次全局呼出变成插件自己的热键事件。找不到/解析不出就安静收场。 */
async function summonPlugin(pluginId: string): Promise<void> {
  let plugins
  try {
    plugins = await listPlugins()
  } catch {
    return // 后端不可达，无键可合成
  }
  const plugin = plugins.find((entry) => entry.id === pluginId && entry.enabled && !entry.broken)
  const field = plugin?.settings_schema.find((entry) => entry.type === 'hotkey')
  if (plugin === undefined || field === undefined) {
    return
  }
  const value = plugin.config[field.key]
  const combo = parseHotkey(typeof value === 'string' ? value : '')
  if (combo === null) {
    return
  }
  window.dispatchEvent(new KeyboardEvent('keydown', syntheticInit(combo)))
}

function navigate(path: string): void {
  window.dispatchEvent(new CustomEvent(SHELL_NAVIGATE_EVENT, { detail: path }))
}

/** App 挂载时调用一次：装上 window.__jarvisShell，壳里再转发配置变更。 */
export function installShellBridge(): void {
  if (typeof window === 'undefined' || window.__jarvisShell !== undefined) {
    return
  }
  window.__jarvisShell = { summonPlugin, navigate }
  window.addEventListener(PLUGINS_CHANGED_EVENT, () => {
    void shellApi()?.refresh_hotkeys()
  })
}

/** 弹窗页（/popup/<pluginId>）里的 pluginId；非弹窗路径给 null。 */
export function popupPluginId(pathname: string): string | null {
  const match = /^\/popup\/(.+?)\/?$/.exec(pathname)
  return match === null ? null : match[1]
}

export interface PopupPin {
  pinned: boolean
  toggle(): void
}

/**
 * 弹窗页签行的「固定」（置顶）状态。返回 null 表示这里没有图钉可渲染：
 * 不在弹窗页（主窗口的页内卡片、浏览器 dev），或还没从壳读到初始值。
 *
 * 固定的真相在 Python（Shell 的 window_state），这里只镜像给按钮着色：
 * 挂载时读一次（pywebview 注入迟于页面脚本，没就绪就等 pywebviewready），
 * 切换时乐观翻转、失败回滚。同一弹窗的新建/详情两个视图各自调一次挂载，
 * 读到的都是 Python 里的最新值，不需要互相穿 props。
 */
export function usePopupPin(): PopupPin | null {
  const [pluginId] = useState(() =>
    typeof window === 'undefined' ? null : popupPluginId(window.location.pathname),
  )
  const [pinned, setPinned] = useState<boolean | null>(null)

  useEffect(() => {
    if (pluginId === null) {
      return
    }
    let alive = true
    function read() {
      try {
        const value = window.pywebview?.api.get_popup_pin(pluginId as string)
        Promise.resolve(value)
          .then((result) => {
            if (alive && typeof result === 'boolean') {
              setPinned(result)
            }
          })
          .catch(() => {})
      } catch {
        // 页面跑在无壳环境（浏览器 dev 的 /popup 降级入口）：没有图钉。
      }
    }
    if (window.pywebview?.api) {
      read()
    } else {
      window.addEventListener('pywebviewready', read, { once: true })
    }
    return () => {
      alive = false
      window.removeEventListener('pywebviewready', read)
    }
  }, [pluginId])

  const toggle = useCallback(() => {
    if (pluginId === null || pinned === null) {
      return
    }
    const next = !pinned
    setPinned(next)
    try {
      Promise.resolve(window.pywebview?.api.set_popup_pin(pluginId, next)).catch(() => {
        setPinned(pinned) // 壳没接住：回滚到切换前的真相
      })
    } catch {
      setPinned(pinned)
    }
  }, [pluginId, pinned])

  if (pluginId === null || pinned === null) {
    return null
  }
  return { pinned, toggle }
}
