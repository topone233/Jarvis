import { describe, expect, it } from 'vitest'

import {
  compactAt,
  createPayload,
  draftFrom,
  effectiveDefault,
  emptyDraft,
  LIMITS,
  modelNames,
  profileValues,
  updatePayload,
  type ProfileDraft,
  type ProfileValues,
} from './profiles'
import type { ModelProfile } from './types'

let next = 0

function profile(over: Partial<ModelProfile> = {}): ModelProfile {
  next += 1
  return {
    id: `p${next}`,
    name: `配置${next}`,
    base_url: 'http://127.0.0.1:8790/v1',
    protocol: 'chat_completions',
    chat_model: 'stub-model',
    embedding_model: null,
    context_window: 128_000,
    output_token_reserve: 8_192,
    max_tokens: null,
    compact_percent: 72,
    thinking_on: {},
    thinking_off: {},
    is_default: false,
    has_api_key: false,
    created_at: `2026-09-12T10:00:0${next}.000000+00:00`,
    updated_at: '2026-09-12T10:00:00.000000+00:00',
    deleted_at: null,
    ...over,
  }
}

/** What the defaults in a fresh draft turn into. */
const VALUES: ProfileValues = {
  context_window: 128_000,
  output_token_reserve: 8_192,
  max_tokens: null,
  compact_percent: 72,
  thinking_on: {},
  thinking_off: {},
}

/** The parameters the draft was accepted as, or a failure saying otherwise. */
function parsed(draft: ProfileDraft): ProfileValues {
  const result = profileValues(draft)
  if (typeof result === 'string') {
    throw new Error(`expected the draft to be accepted, got: ${result}`)
  }
  return result
}

/** The complaint, or a failure naming the draft that was accepted instead. */
function complaint(draft: ProfileDraft): string {
  const result = profileValues(draft)
  if (typeof result !== 'string') {
    throw new Error(`expected a complaint, got ${JSON.stringify(result)}`)
  }
  return result
}

describe('opening a profile in the form', () => {
  it('fills in what the API knows and leaves the key empty', () => {
    const draft = draftFrom(profile({ name: '本地模型', has_api_key: true }))
    expect(draft).toEqual({
      id: 'p1',
      name: '本地模型',
      baseUrl: 'http://127.0.0.1:8790/v1',
      chatModel: 'stub-model',
      embeddingModel: '',
      apiKey: '',
      contextWindow: '128000',
      outputTokenReserve: '8192',
      maxTokens: '',
      compactPercent: '72',
      thinkingOn: '',
      thinkingOff: '',
    })
  })

  it('shows a number that is set and empty for one that is not', () => {
    const draft = draftFrom(profile({ max_tokens: 4096, compact_percent: 40 }))
    expect(draft.maxTokens).toBe('4096')
    expect(draft.compactPercent).toBe('40')
  })

  it('shows a fragment as indented JSON, and an empty one as a blank box', () => {
    const draft = draftFrom(profile({ thinking_off: { enable_thinking: false } }))

    expect(draft.thinkingOff).toBe('{\n  "enable_thinking": false\n}')
    // Blank rather than "{}": the common case is a profile that says nothing,
    // and a box holding "{}" looks like something that has to be deleted.
    expect(draft.thinkingOn).toBe('')
  })

  it('gives back what it was shown', () => {
    const fragment = { thinking: { type: 'disabled', budget_tokens: 0 } }
    const draft = draftFrom(profile({ thinking_off: fragment }))

    expect(parsed(draft).thinking_off).toEqual(fragment)
  })
})

describe('the call parameters', () => {
  it('turns the four boxes into numbers', () => {
    expect(parsed({ ...emptyDraft(), maxTokens: '4096' })).toEqual({
      ...VALUES,
      max_tokens: 4096,
    })
  })

  it('reads an empty generation limit as no limit at all', () => {
    expect(parsed({ ...emptyDraft(), maxTokens: '   ' }).max_tokens).toBeNull()
  })

  const BAD: Array<[string, Partial<ProfileDraft>]> = [
    ['a cleared context window', { contextWindow: '' }],
    ['a context window that is not a number', { contextWindow: '128k' }],
    ['a context window below the floor', { contextWindow: String(LIMITS.contextWindow.min - 1) }],
    ['a context window above the ceiling', { contextWindow: String(LIMITS.contextWindow.max + 1) }],
    ['a decimal context window', { contextWindow: '128000.5' }],
    ['a negative context window', { contextWindow: '-1' }],
    ['an output reserve below the floor', { outputTokenReserve: '255' }],
    // Zero input budget: whatever is sent, there is no room left to send it in.
    ['an output reserve as large as the context', { outputTokenReserve: '128000' }],
    ['an output reserve larger than the context', { outputTokenReserve: '200000' }],
    ['a generation limit of zero', { maxTokens: '0' }],
    ['a generation limit that is not a number', { maxTokens: 'unlimited' }],
    ['a generation limit above the ceiling', { maxTokens: '1000001' }],
    ['a compact percent below the floor', { compactPercent: '9' }],
    ['a compact percent above the ceiling', { compactPercent: '96' }],
    ['a cleared compact percent', { compactPercent: '' }],
  ]

  it.each(BAD)('refuses %s', (_what, over) => {
    expect(complaint({ ...emptyDraft(), ...over })).toMatch(/要填|要留空|要小于/)
  })
})

describe('the thinking fragments', () => {
  it('are empty objects when the boxes are blank', () => {
    // Not "send nothing": the request builder spreads an empty object, which is
    // how "this endpoint needs no instructions" reaches the wire as no fields.
    expect(parsed(emptyDraft()).thinking_on).toEqual({})
    expect(parsed(emptyDraft()).thinking_off).toEqual({})
  })

  it('accept what the user typed, whatever the keys are', () => {
    const draft = { ...emptyDraft(), thinkingOn: '{"enable_thinking": true}' }
    expect(parsed(draft).thinking_on).toEqual({ enable_thinking: true })
  })

  it('keep a nested dialect exactly as written', () => {
    const draft = { ...emptyDraft(), thinkingOff: '{"thinking": {"type": "disabled"}}' }
    expect(parsed(draft).thinking_off).toEqual({ thinking: { type: 'disabled' } })
  })

  const BAD_FRAGMENT = [
    ['a half-typed object', '{"enable_thinking":'],
    ['an array', '[{"enable_thinking": false}]'],
    ['a bare string', '"off"'],
    ['a number', '0'],
    ['null', 'null'],
  ]

  it.each(BAD_FRAGMENT)('refuse %s', (_what, text) => {
    expect(complaint({ ...emptyDraft(), thinkingOff: text })).toContain('JSON 对象')
    expect(complaint({ ...emptyDraft(), thinkingOn: text })).toContain('JSON 对象')
  })

  it('report the offending fragment by which one it is', () => {
    expect(complaint({ ...emptyDraft(), thinkingOn: 'oops' })).toContain('思考开')
    expect(complaint({ ...emptyDraft(), thinkingOff: 'oops' })).toContain('思考关')
  })

  it('treat whitespace as a blank box', () => {
    expect(parsed({ ...emptyDraft(), thinkingOff: '  \n ' }).thinking_off).toEqual({})
  })
})

describe('the model names in a /models response', () => {
  it('takes the ids, in the order the service sent them', () => {
    expect(modelNames([{ id: 'qwen3' }, { id: 'qwen3-mini' }])).toEqual(['qwen3', 'qwen3-mini'])
  })

  it('drops anything that is not a usable name', () => {
    // An entry with no id, an id that is not a string, an empty one and a
    // repeat: none of them is something to put in a dropdown.
    const models = [{ id: 'qwen3' }, {}, { id: 7 }, { id: '' }, { id: 'qwen3' }, null, 'qwen3']
    expect(modelNames(models)).toEqual(['qwen3'])
  })

  it('has nothing to say about an empty list', () => {
    expect(modelNames([])).toEqual([])
  })
})

describe('when the conversation would be compacted', () => {
  it('is the configured share of the input budget', () => {
    // (128000 - 8192) * 72% = 86261.76, truncated the way the backend truncates.
    expect(compactAt(emptyDraft())).toBe(86_261)
  })

  it('never goes below the floor the backend applies', () => {
    // A small window would otherwise put the threshold under any real
    // conversation, and every message would arrive already compacted.
    const draft = { ...emptyDraft(), contextWindow: '4096', outputTokenReserve: '256' }
    expect(compactAt({ ...draft, compactPercent: '10' })).toBe(2_000)
  })

  it('has nothing to say while a box is empty or holds something else', () => {
    expect(compactAt({ ...emptyDraft(), contextWindow: '' })).toBeNull()
    expect(compactAt({ ...emptyDraft(), compactPercent: '七十二' })).toBeNull()
    // Not a reachable save - `profileValues` refuses it - but the hint is drawn
    // from the boxes as they are typed, so it has to survive being asked.
    expect(compactAt({ ...emptyDraft(), outputTokenReserve: '200000' })).toBeNull()
  })
})

describe('editing a profile', () => {
  it('leaves the key out of the request when the box was not touched', () => {
    const draft = draftFrom(profile({ name: '改个名字' }))
    const payload = updatePayload({ ...draft, name: '新名字' }, VALUES)
    expect(payload).toEqual({
      name: '新名字',
      base_url: 'http://127.0.0.1:8790/v1',
      chat_model: 'stub-model',
      embedding_model: null,
      ...VALUES,
    })
    // The whole point: absent, not empty. An explicit "" deletes the stored key.
    expect('api_key' in payload).toBe(false)
  })

  it('never sends an empty or whitespace-only key', () => {
    const draft = { ...emptyDraft(), id: 'p1', name: 'a', apiKey: '   ' }
    expect('api_key' in updatePayload(draft, VALUES)).toBe(false)
  })

  it('sends the key when one was typed, trimmed', () => {
    const draft = { ...emptyDraft(), id: 'p1', name: 'a', apiKey: ' sk-123 ' }
    expect(updatePayload(draft, VALUES).api_key).toBe('sk-123')
  })

  it('trims the three required fields', () => {
    const payload = updatePayload(
      {
        ...emptyDraft(),
        id: 'p1',
        name: '  本地  ',
        baseUrl: ' http://127.0.0.1:8790/v1 ',
        chatModel: ' stub-model ',
      },
      VALUES,
    )
    expect(payload).toMatchObject({
      name: '本地',
      base_url: 'http://127.0.0.1:8790/v1',
      chat_model: 'stub-model',
    })
  })

  it('carries the call parameters on every save, changed or not', () => {
    // Whole-object rather than per-field: a PATCH that mentions only some of them
    // would leave the rest at whatever the last save said, which is not what the
    // boxes on screen are showing.
    const payload = updatePayload(draftFrom(profile()), {
      context_window: 32_000,
      output_token_reserve: 4_096,
      max_tokens: 2_048,
      compact_percent: 50,
      thinking_on: {},
      thinking_off: {},
    })
    expect(payload).toMatchObject({
      context_window: 32_000,
      output_token_reserve: 4_096,
      max_tokens: 2_048,
      compact_percent: 50,
    })
  })

  it('sends both fragments, so clearing one actually clears it', () => {
    // An absent key means "leave what is stored alone", which is the opposite of
    // what emptying the box means.
    const payload = updatePayload(draftFrom(profile({ thinking_off: { a: 1 } })), {
      ...VALUES,
      thinking_off: {},
    })
    expect(payload.thinking_off).toEqual({})
  })
})

describe('adding a profile', () => {
  it('makes the very first one the default', () => {
    const payload = createPayload({ ...emptyDraft(), name: '本地', apiKey: 'k' }, true, VALUES)
    expect(payload.is_default).toBe(true)
  })

  it('does not touch the default when one already exists', () => {
    const payload = createPayload({ ...emptyDraft(), name: '另一个' }, false, VALUES)
    expect('is_default' in payload).toBe(false)
  })

  it('sends null rather than an empty key, because there is nothing to preserve', () => {
    expect(createPayload(emptyDraft(), true, VALUES).api_key).toBeNull()
  })

  it('carries the call parameters it was given', () => {
    const payload = createPayload(emptyDraft(), true, {
      ...VALUES,
      context_window: 200_000,
      max_tokens: 8_192,
    })
    expect(payload).toMatchObject({ context_window: 200_000, max_tokens: 8_192 })
  })
})

describe('which profile is in effect', () => {
  it('is the flagged one even when it is not the oldest', () => {
    const older = profile({ created_at: '2026-09-01T00:00:00.000000+00:00' })
    const flagged = profile({ is_default: true })
    expect(effectiveDefault([older, flagged])).toBe(flagged)
  })

  it('falls back to the oldest when nothing is flagged', () => {
    const oldest = profile({ created_at: '2026-09-01T00:00:00.000000+00:00' })
    const newest = profile({ created_at: '2026-09-20T00:00:00.000000+00:00' })
    expect(effectiveDefault([newest, oldest, profile()])).toBe(oldest)
  })

  it('has nothing to say about an empty list', () => {
    expect(effectiveDefault([])).toBeNull()
  })
})

describe('embedding model', () => {
  it('round-trips from the profile into the payloads', () => {
    const draft = draftFrom(profile({ embedding_model: ' bge-m3 ' }))
    expect(draft.embeddingModel).toBe(' bge-m3 ')
    const values = parsed(draft)
    expect(createPayload(draft, false, values).embedding_model).toBe('bge-m3')
    expect(updatePayload(draft, values).embedding_model).toBe('bge-m3')
  })

  it('clears the stored model when the box is emptied', () => {
    const draft = draftFrom(profile({ embedding_model: 'bge-m3' }))
    draft.embeddingModel = ''
    const values = parsed(draft)
    expect(createPayload(draft, false, values).embedding_model).toBeNull()
    expect(updatePayload(draft, values).embedding_model).toBeNull()
  })

  it('a fresh draft sends null, not a name', () => {
    const draft = emptyDraft()
    draft.name = '本地'
    draft.baseUrl = 'http://127.0.0.1:8790/v1'
    draft.chatModel = 'stub-model'
    const values = parsed(draft)
    expect(createPayload(draft, true, values).embedding_model).toBeNull()
  })
})
