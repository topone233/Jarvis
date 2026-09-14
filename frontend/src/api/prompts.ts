/**
 * The prompts tab's rules, kept out of the component and tested.
 *
 * There are three prompts and they are independent: saving one must not claim
 * to have saved the others, and a prompt nobody touched must not be sent at
 * all. "Which of these have actually changed" is the whole of the logic, and it
 * is the part that would be tedious to check by hand.
 */

import type { AppSettings, PromptKey, PromptPatch } from './types'

export interface PromptField {
  key: PromptKey
  title: string
  hint: string
}

/**
 * The three, in the order they are shown.
 *
 * The titles and hints live here rather than on the server: it sends the keys
 * and the text, and what to call them on screen is the screen's business.
 */
export const PROMPT_FIELDS: PromptField[] = [
  {
    key: 'system_prompt',
    title: '系统提示词',
    hint: '每轮对话最前面的那段话，决定了 Jarvis 是谁、怎么说话。清空后就不发这一段。',
  },
  {
    key: 'compaction_prompt',
    title: '上下文压缩提示词',
    hint: '对话变长时，用它把早先的内容整理成一段摘要，之后只带着摘要继续。',
  },
  {
    key: 'memory_prompt',
    title: '记忆管理提示词',
    hint: '告诉模型何时在回答末尾附一段 memory JSON 来记住或忘记信息。改动它要留意格式。',
  },
]

/** Just the prompts that differ from what is stored, as a save body. */
export function changedPrompts(
  prompts: AppSettings['prompts'],
  drafts: Record<string, string | undefined>,
): PromptPatch {
  const patch: PromptPatch = {}
  for (const field of PROMPT_FIELDS) {
    const text = drafts[field.key]
    if (text !== undefined && text !== prompts[field.key].text) {
      patch[field.key] = text
    }
  }
  return patch
}

/** The text to draw in a prompt's box: the edit in progress, or the stored one. */
export function shownText(
  prompts: AppSettings['prompts'],
  drafts: Record<string, string | undefined>,
  key: PromptKey,
): string {
  return drafts[key] ?? prompts[key].text
}

/**
 * One prompt back to the text the code ships.
 *
 * Built by assignment rather than as an object literal, because a computed key
 * of a union type is not narrowed to one property - `{ [key]: null }` widens to
 * an index signature, which is a different shape from the three-field patch the
 * API expects.
 */
export function restorePatch(key: PromptKey): PromptPatch {
  const patch: PromptPatch = {}
  patch[key] = null
  return patch
}
