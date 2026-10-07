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

import { listPlugins } from '../api/plugins'
import { parseHotkey, syntheticInit } from './hotkey'
import { PLUGINS_CHANGED_EVENT } from './registry'

/** pywebview 注入的 JS API（只在桌面壳里存在），只声明我们用到的部分。 */
interface PywebviewApi {
  api: {
    refresh_hotkeys(): unknown
    hide_popup(pluginId: string): unknown
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
