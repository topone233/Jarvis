/**
 * 呼出便签的快捷键：字符串契约（"Alt+N"）、按键事件匹配、录制。
 *
 * 全部是纯函数——弹窗的监听和设置页的录制框都依赖这里，解析和匹配说好了
 * 用同一套语义，测试把它们钉死。
 *
 * 约定：至少一个修饰键。一个没有修饰键的呼出键会在正常打字时不断触发弹窗，
 * 所以解析时直接拒绝纯单键。
 */

export interface HotkeyCombo {
  ctrl: boolean
  alt: boolean
  shift: boolean
  meta: boolean
  /** 主键，单字符（字母/数字/符号）或功能键名（F1…F12）。 */
  key: string
}

/** The modifiers a combo can name, canonical order matters for display. */
const MODIFIERS = ['ctrl', 'alt', 'shift', 'meta'] as const

const FUNCTION_KEY = /^F([1-9]|1[0-2])$/

/** "Alt+N" → combo；说明不了主键（纯修饰键、空、乱写）就给 null。 */
export function parseHotkey(text: string): HotkeyCombo | null {
  const parts = text
    .split('+')
    .map((part) => part.trim().toLowerCase())
    .filter((part) => part !== '')
  const combo: HotkeyCombo = { ctrl: false, alt: false, shift: false, meta: false, key: '' }
  for (const part of parts) {
    if (MODIFIERS.includes(part as (typeof MODIFIERS)[number])) {
      combo[part as (typeof MODIFIERS)[number]] = true
      continue
    }
    if (combo.key !== '') {
      return null // 两个主键说不通
    }
    if (FUNCTION_KEY.test(part.toUpperCase())) {
      combo.key = part.toUpperCase()
      continue
    }
    if (part.length !== 1 || !/[a-z0-9]/.test(part)) {
      return null // 只接受单字母、数字，其余键位留给以后有需要再说
    }
    combo.key = part.toUpperCase()
  }
  // 字母和数字必须带修饰键（否则打字就触发），功能键单独一个就成立——
  // 没有人用 F9 打字。
  const isFunctionKey = FUNCTION_KEY.test(combo.key)
  if (combo.key === '' || (!isFunctionKey && !combo.ctrl && !combo.alt && !combo.shift && !combo.meta)) {
    return null
  }
  return combo
}

/** 归一化的显示串，修饰键按固定顺序，主键大写。 */
export function formatHotkey(combo: HotkeyCombo): string {
  const parts = MODIFIERS.filter((modifier) => combo[modifier]).map((modifier) =>
    modifier === 'ctrl' ? 'Ctrl' : modifier === 'meta' ? 'Win' : modifier[0].toUpperCase() + modifier.slice(1),
  )
  return [...parts, combo.key].join('+')
}

function eventKeyOf(comboKey: string, event: KeyboardEvent): string {
  // 字母键在 Shift 按下时 event.key 是大写、在中文输入法下也可能异常，
  // code（KeyN）是唯一稳定的；功能键没有这个问题，按 key 名直接比对。
  if (FUNCTION_KEY.test(comboKey)) {
    return event.key
  }
  if (/^[A-Z]$/.test(comboKey) && event.code.toLowerCase() === `key${comboKey.toLowerCase()}`) {
    return comboKey
  }
  return event.key.length === 1 ? event.key.toUpperCase() : event.key
}

/** 这个键盘事件是否就是该组合键。修饰键必须精确一致——Ctrl+Shift+N 不是 Alt+N。 */
export function hotkeyMatches(combo: HotkeyCombo, event: KeyboardEvent): boolean {
  return (
    combo.ctrl === event.ctrlKey &&
    combo.alt === event.altKey &&
    combo.shift === event.shiftKey &&
    combo.meta === event.metaKey &&
    eventKeyOf(combo.key, event) === combo.key
  )
}

/**
 * 录制：把一次按键变成组合键串。纯修饰键给 null（还没按到主键），
 * Esc 也给 null——它是"取消录制"的信号，不该被当成快捷键。规则与
 * parseHotkey 一致：字母数字要带修饰键，功能键可以单独成为呼出键。
 */
export function describeEvent(event: KeyboardEvent): string | null {
  if (['Control', 'Alt', 'Shift', 'Meta'].includes(event.key)) {
    return null
  }
  if (event.key === 'Escape') {
    return null
  }
  const combo: HotkeyCombo = {
    ctrl: event.ctrlKey,
    alt: event.altKey,
    shift: event.shiftKey,
    meta: event.metaKey,
    key: event.key.length === 1 ? event.key.toUpperCase() : event.key,
  }
  if (!FUNCTION_KEY.test(combo.key) && !/^[A-Z0-9]$/.test(combo.key)) {
    return null
  }
  const isFunctionKey = FUNCTION_KEY.test(combo.key)
  if (!isFunctionKey && !combo.ctrl && !combo.alt && !combo.shift && !combo.meta) {
    return null
  }
  return formatHotkey(combo)
}
