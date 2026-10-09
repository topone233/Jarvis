import { describe, expect, it } from 'vitest'

import { popupPluginId } from './shell'

// 弹窗页靠 pathname 认自己：usePopupPin 与拖拽类都以它分流（浏览器 dev、
// 主窗口页内卡片给 null，行为零变化）。
describe('popupPluginId', () => {
  it('从弹窗路径解析插件 id', () => {
    expect(popupPluginId('/popup/notepad')).toBe('notepad')
  })

  it('容忍结尾斜杠', () => {
    expect(popupPluginId('/popup/notepad/')).toBe('notepad')
  })

  it('非弹窗路径给 null', () => {
    expect(popupPluginId('/')).toBeNull()
    expect(popupPluginId('/notes')).toBeNull()
    expect(popupPluginId('/popup/')).toBeNull()
  })
})
