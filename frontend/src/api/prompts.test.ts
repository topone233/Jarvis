import { describe, expect, it } from 'vitest'

import { changedPrompts, PROMPT_FIELDS, restorePatch, shownText } from './prompts'
import type { AppSettings, PromptSetting } from './types'

function prompt(text: string, isDefault = false): PromptSetting {
  return { text, default_text: '内置的那一份', is_default: isDefault }
}

function settings(over: Partial<AppSettings['prompts']> = {}): AppSettings['prompts'] {
  return {
    system_prompt: prompt('你是 Jarvis。'),
    compaction_prompt: prompt('把早先的对话整理成摘要。'),
    memory_prompt: prompt('挑出值得记住的信息。'),
    ...over,
  }
}

describe('what a save sends', () => {
  it('sends nothing when nothing was typed', () => {
    expect(changedPrompts(settings(), {})).toEqual({})
  })

  it('sends nothing for a prompt typed back to what is stored', () => {
    expect(changedPrompts(settings(), { system_prompt: '你是 Jarvis。' })).toEqual({})
  })

  it('sends only the prompt that differs', () => {
    const patch = changedPrompts(settings(), {
      system_prompt: '你是 Jarvis，说话简短。',
      memory_prompt: '挑出值得记住的信息。',
    })
    expect(patch).toEqual({ system_prompt: '你是 Jarvis，说话简短。' })
  })

  it('sends a cleared prompt as an empty string, which is a real edit', () => {
    // Not the same as restoring: this is the user saying "send no system
    // message", and the backend carries it out by leaving the section out.
    expect(changedPrompts(settings(), { system_prompt: '' })).toEqual({ system_prompt: '' })
  })

  it('sends nothing for a box that was opened but never touched', () => {
    // `undefined` is "no draft", which is not the same as an empty one, and the
    // difference is the entire reason this function exists.
    expect(changedPrompts(settings(), { compaction_prompt: undefined })).toEqual({})
  })

  it('ignores a key that is not one of the prompts', () => {
    expect(changedPrompts(settings(), { temperature: '0.7' })).toEqual({})
  })

  it('can send more than one at once', () => {
    const patch = changedPrompts(settings(), {
      compaction_prompt: '换个压缩提示词。',
      memory_prompt: '换个记忆提示词。',
    })
    expect(Object.keys(patch).sort()).toEqual(['compaction_prompt', 'memory_prompt'])
  })
})

describe('what a box draws', () => {
  it('is the stored text until something is typed', () => {
    expect(shownText(settings(), {}, 'system_prompt')).toBe('你是 Jarvis。')
  })

  it('is the edit in progress once there is one', () => {
    expect(shownText(settings(), { system_prompt: '半句话' }, 'system_prompt')).toBe('半句话')
  })

  it('draws an empty box after it was cleared, not the stored text again', () => {
    expect(shownText(settings(), { system_prompt: '' }, 'system_prompt')).toBe('')
  })
})

describe('the fields on screen', () => {
  it('names each prompt once', () => {
    const keys = PROMPT_FIELDS.map((field) => field.key)
    expect(new Set(keys).size).toBe(keys.length)
  })

  it('can put any of them back on its own', () => {
    // Null rather than the default text: restoring deletes the stored row, so
    // "the default" stays whatever the code says it is and can never go stale.
    expect(restorePatch('compaction_prompt')).toEqual({ compaction_prompt: null })
  })
})
