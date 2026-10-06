/**
 * 插件的前端注册表：`plugin/<id>/frontend/index.tsx` 在这里被发现的。
 *
 * 后端已经把每个插件的文件夹当作一个插件（开关、配置、路由），这份注册表
 * 是它的前端一半：每个插件的入口模块导出 meta（导航标题/路径/图标）、一个
 * 整页组件（默认导出）、可选的 QuickCapture 全局组件。Vite 的
 * `import.meta.glob` 把候选入口变成一串懒加载函数——开发时新建插件文件夹
 * 即刻可见，生产构建则把当时存在的插件打进去（重新 build 才会带上新插件，
 * 这条边界在设置页如实展示）。
 *
 * 只有「后端启用 且 前端有注册」的插件会出现在界面里；后端挂了或还没配置
 * 时注册表为空，导航与弹窗随之消失——这是插件的开关语义，不是错误。
 */

import { useEffect, useState } from 'react'
import type { ComponentType } from 'react'

import { listPlugins, type PluginInfo } from '../api/plugins'

/** A plugin's frontend entry module. Everything except `meta` is optional. */
export interface PluginModule {
  meta: PluginMeta
  default: ComponentType<{ onClose(): void }>
  quickCapture?: ComponentType
}

export interface PluginMeta {
  navLabel: string
  navPath: string
  navIcon: ComponentType<{ size?: number }>
}

/** What the shell renders for one plugin: nav row, route, optional popup. */
export interface PluginFrontend {
  id: string
  meta: PluginMeta
  Page: ComponentType<{ onClose(): void }>
  quickCapture?: ComponentType
}

/** Dispatched after any plugin switch/config/rescan so live UI re-reads. */
export const PLUGINS_CHANGED_EVENT = 'jarvis-plugins-changed'

export function notifyPluginsChanged(): void {
  window.dispatchEvent(new Event(PLUGINS_CHANGED_EVENT))
}

const entries = import.meta.glob<PluginModule>('../../../plugin/*/frontend/index.ts*')

/** The plugin id a glob key belongs to: `…/plugin/notepad/frontend/index.tsx`. */
function idOf(key: string): string {
  const match = /\/plugin\/([^/]+)\/frontend\/index\.tsx?$/.exec(key)
  return match === null ? '' : match[1]
}

/** The lazy loader for one plugin's entry module, or null if it has none. */
function loaderOf(id: string): (() => Promise<PluginModule>) | null {
  const key = Object.keys(entries).find((candidate) => idOf(candidate) === id)
  return key === undefined ? null : entries[key]
}

/** Load and validate one plugin's frontend; a bad entry module is skipped. */
async function loadFrontend(id: string): Promise<PluginFrontend | null> {
  const load = loaderOf(id)
  if (load === null) {
    return null
  }
  try {
    const module = await load()
    const meta = module.meta
    if (
      typeof meta?.navLabel !== 'string' ||
      typeof meta?.navPath !== 'string' ||
      typeof meta?.navIcon !== 'function' ||
      typeof module.default !== 'function'
    ) {
      console.error(`插件 ${id} 的前端入口缺少 meta 或页面组件。`)
      return null
    }
    return {
      id,
      meta,
      Page: module.default,
      quickCapture: typeof module.quickCapture === 'function' ? module.quickCapture : undefined,
    }
  } catch (error) {
    console.error(`插件 ${id} 的前端加载失败。`, error)
    return null
  }
}

/**
 * The enabled plugins that also have a frontend, loaded.
 *
 * Re-reads when plugin settings change (a switch here must remove the nav
 * row at once) and when the plugin list itself changes identity.
 *
 * `ready` is false until the first read settles. It matters: with an empty
 * list the shell's catch-all route would redirect a deep link like /notes
 * to / before the plugin route exists — F5 on a plugin page would bounce
 * home. The shell holds that redirect back until this flag is true.
 */
export function usePluginFrontends(): { frontends: PluginFrontend[]; ready: boolean } {
  const [frontends, setFrontends] = useState<PluginFrontend[]>([])
  const [ready, setReady] = useState(false)

  useEffect(() => {
    let alive = true
    async function refresh() {
      let plugins: PluginInfo[]
      try {
        plugins = await listPlugins()
      } catch {
        if (alive) {
          setFrontends([])
          setReady(true)
        }
        return
      }
      const ids = plugins
        .filter((plugin) => plugin.enabled && !plugin.broken)
        .map((plugin) => plugin.id)
      const loaded = await Promise.all(ids.map(loadFrontend))
      if (alive) {
        setFrontends(loaded.filter((entry): entry is PluginFrontend => entry !== null))
        setReady(true)
      }
    }
    void refresh()
    window.addEventListener(PLUGINS_CHANGED_EVENT, refresh)
    return () => {
      alive = false
      window.removeEventListener(PLUGINS_CHANGED_EVENT, refresh)
    }
  }, [])

  return { frontends, ready }
}
