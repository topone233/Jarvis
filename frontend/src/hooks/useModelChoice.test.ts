/**
 * The choice rules that are not visible in the hook's shape:
 *
 * - what「设为默认」does to a pin that points somewhere else (`choiceAfterDefault`);
 * - how the dropdown's sections are built and ordered (`buildModelGroups`);
 * - how a dropdown row names the (profile, model) pair it picks
 *   (`modelOptionId` / `parseModelOptionId`).
 *
 * The hook itself stays untested: it owns fetches and module state. Everything
 * with a decision in it lives in the three functions above, pure by design.
 */

import { describe, expect, it } from 'vitest'

import type { ModelProfile } from '../api/types'
import {
  buildModelGroups,
  choiceAfterDefault,
  modelOptionId,
  parseModelOptionId,
  type StoredChoice,
} from './useModelChoice'

function profile(overrides: Partial<ModelProfile> & Pick<ModelProfile, 'id' | 'name' | 'chat_model'>): ModelProfile {
  return {
    base_url: 'https://api.example.com/v1',
    protocol: 'chat_completions',
    context_window: 128_000,
    output_token_reserve: 8_192,
    max_tokens: null,
    compact_percent: 72,
    thinking_on: {},
    thinking_off: {},
    is_default: false,
    has_api_key: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    deleted_at: null,
    ...overrides,
  }
}

function choice(overrides: Partial<StoredChoice>): StoredChoice {
  return { profileId: null, chatModel: '', level: 'off', ...overrides }
}

describe('choiceAfterDefault', () => {
  it('clears a pin on another profile, model name included', () => {
    const current = choice({ profileId: 'old', chatModel: 'old-model', level: 'high' })
    expect(choiceAfterDefault(current, 'new')).toEqual({
      profileId: null,
      chatModel: '',
      level: 'high',
    })
  })

  it('keeps a pin on the profile being named default - the pick was the more specific wish', () => {
    const current = choice({ profileId: 'a', chatModel: 'picked-model', level: 'low' })
    expect(choiceAfterDefault(current, 'a')).toBe(current)
  })

  it('leaves an unpinned choice alone', () => {
    const current = choice({ chatModel: '', level: 'medium' })
    expect(choiceAfterDefault(current, 'a')).toBe(current)
  })
})

describe('buildModelGroups', () => {
  it('puts the default profile first and marks its header, keeping the rest in order', () => {
    const groups = buildModelGroups(
      [
        profile({ id: 'a', name: '甲', chat_model: 'm-a' }),
        profile({ id: 'b', name: '乙', chat_model: 'm-b', is_default: true }),
        profile({ id: 'c', name: '丙', chat_model: 'm-c' }),
      ],
      {},
      null,
      '',
    )
    expect(groups.map((group) => group.profileId)).toEqual(['b', 'a', 'c'])
    expect(groups.map((group) => group.label)).toEqual(['乙 · 默认', '甲', '丙'])
  })

  it('offers a profile with no list from its endpoint as its own model name', () => {
    const groups = buildModelGroups([profile({ id: 'a', name: '甲', chat_model: 'm-a' })], {}, null, '')
    expect(groups[0]?.models).toEqual(['m-a'])
  })

  it('uses the endpoint list when one arrived', () => {
    const groups = buildModelGroups(
      [profile({ id: 'a', name: '甲', chat_model: 'm-a' })],
      { a: ['listed-1', 'listed-2'] },
      null,
      '',
    )
    expect(groups[0]?.models).toEqual(['listed-1', 'listed-2'])
  })

  it('prepends the pick to the answering profile section when the endpoint does not list it', () => {
    const groups = buildModelGroups(
      [profile({ id: 'a', name: '甲', chat_model: 'm-a' })],
      { a: ['listed-1'] },
      'a',
      'custom-model',
    )
    expect(groups[0]?.models).toEqual(['custom-model', 'listed-1'])
  })

  it('does not duplicate the pick when the endpoint lists it', () => {
    const groups = buildModelGroups(
      [profile({ id: 'a', name: '甲', chat_model: 'm-a' })],
      { a: ['custom-model', 'listed-1'] },
      'a',
      'custom-model',
    )
    expect(groups[0]?.models).toEqual(['custom-model', 'listed-1'])
  })

  it('does not carry the pick into another profile section', () => {
    const groups = buildModelGroups(
      [
        profile({ id: 'a', name: '甲', chat_model: 'm-a' }),
        profile({ id: 'b', name: '乙', chat_model: 'm-b' }),
      ],
      { a: ['listed-a'], b: ['listed-b'] },
      'a',
      'pick-from-a',
    )
    expect(groups.find((group) => group.profileId === 'b')?.models).toEqual(['listed-b'])
  })

  it('treats an empty endpoint list as no list', () => {
    const groups = buildModelGroups(
      [profile({ id: 'a', name: '甲', chat_model: 'm-a' })],
      { a: [] },
      null,
      '',
    )
    expect(groups[0]?.models).toEqual(['m-a'])
  })
})

describe('modelOptionId / parseModelOptionId', () => {
  it('round-trips a (profile, model) pair', () => {
    const id = modelOptionId('0a3f9c1e-5b7d-4c2a-9e1f-2b3c4d5e6f70', 'deepseek-chat')
    expect(parseModelOptionId(id)).toEqual({
      profileId: '0a3f9c1e-5b7d-4c2a-9e1f-2b3c4d5e6f70',
      model: 'deepseek-chat',
    })
  })

  it('survives a model name containing the separator', () => {
    const id = modelOptionId('profile-a', 'org::weird-model')
    expect(parseModelOptionId(id)).toEqual({ profileId: 'profile-a', model: 'org::weird-model' })
  })

  it('rejects ids that are not one of ours', () => {
    expect(parseModelOptionId('deepseek-chat')).toBeNull()
    expect(parseModelOptionId('::deepseek-chat')).toBeNull()
    expect(parseModelOptionId('profile-a::')).toBeNull()
  })
})
