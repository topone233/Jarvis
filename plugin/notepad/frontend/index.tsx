/**
 * 便签插件的前端注册：导航图标、整页编辑器、全局呼出弹窗，外加本插件的
 * 全部样式。宿主的 registry 懒加载这个模块。Milkdown 随插件块一起下载
 * （页面与弹窗共用同一个块），但编辑器实例只在 NotepadPage 或弹窗详情
 * （NoteDetail）真正挂载时才创建——打开呼出卡片本身没有这份开销。
 */

import './notepad.css'

import NotepadPage from './NotepadPage'
import { QuickCapture } from './QuickCapture'
import type { SVGProps } from 'react'

import type { PluginMeta, PluginModule } from '../../../frontend/src/plugins/registry'

function NotebookIcon(props: SVGProps<SVGSVGElement> & { size?: number }) {
  return (
    <svg
      width={props.size ?? 18}
      height={props.size ?? 18}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      {/* 一张右下角折起来的便签纸。 */}
      <path d="M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v7L13.5 20H6.5A2.5 2.5 0 0 1 4 17.5Z" />
      <path d="M13.5 20v-4a2 2 0 0 1 2-2H20" />
    </svg>
  )
}

export const meta: PluginMeta = {
  navLabel: '便签',
  navPath: '/notes',
  navIcon: NotebookIcon,
}

export default NotepadPage

export const quickCapture = QuickCapture

// Compile-time check that this module keeps the shape the registry validates.
const _contract: PluginModule = { meta, default: NotepadPage, quickCapture }
void _contract
