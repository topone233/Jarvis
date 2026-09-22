import { describe, expect, it } from 'vitest'

import {
  clearPatch,
  draftFromSetting,
  savePatch,
  testPayload,
  thresholdsDraftFrom,
  thresholdsError,
  thresholdsPayload,
  THRESHOLDS_DEFAULT_PATCH,
  type RetrievalDraft,
  type ThresholdDraft,
} from './retrieval'
import type { RetrievalModelSetting, RetrievalSettings } from './types'

const EMBEDDING: RetrievalModelSetting = {
  base_url: 'http://emb.local/v1',
  model: 'emb-1',
  has_api_key: true,
}

const SETTINGS: RetrievalSettings = {
  embedding: EMBEDDING,
  rerank: null,
  thresholds: {
    memory_floor: { value: 0.55, default: 0.55, is_default: true },
    semantic_floor: { value: 0.5, default: 0.5, is_default: true },
    rerank_floor: { value: 0.25, default: 0.25, is_default: true },
  },
}

describe('opening a card in the form', () => {
  it('fills in what the API knows and leaves the key empty', () => {
    expect(draftFromSetting(EMBEDDING)).toEqual({
      baseUrl: 'http://emb.local/v1',
      model: 'emb-1',
      apiKey: '',
    })
  })

  it('opens blank when the kind is not configured', () => {
    expect(draftFromSetting(null)).toEqual({ baseUrl: '', model: '', apiKey: '' })
  })
})

describe('building a save', () => {
  it('sends one kind and not the other, so a card saves alone', () => {
    const patch = savePatch('rerank', {
      baseUrl: 'http://rr.local/v1',
      model: 'rr-1',
      apiKey: '',
    })
    expect(patch).toEqual({ rerank: { base_url: 'http://rr.local/v1', model: 'rr-1' } })
    expect('embedding' in (patch as object)).toBe(false)
  })

  it('leaves the key out when the box is blank, so the stored one survives', () => {
    const patch = savePatch('embedding', {
      baseUrl: 'http://emb.local/v1',
      model: 'emb-2',
      apiKey: '',
    })
    expect('api_key' in (patch as { embedding: object }).embedding).toBe(false)
  })

  it('sends the key when one was typed, trimmed', () => {
    const patch = savePatch('embedding', {
      baseUrl: ' http://emb.local/v1 ',
      model: ' emb-1 ',
      apiKey: ' sk-123 ',
    })
    expect(patch).toEqual({
      embedding: { base_url: 'http://emb.local/v1', model: 'emb-1', api_key: 'sk-123' },
    })
  })

  it('refuses a blank endpoint or model before anything is sent', () => {
    expect(savePatch('embedding', { baseUrl: '   ', model: 'emb-1', apiKey: '' })).toContain(
      '接口地址',
    )
    expect(
      savePatch('embedding', { baseUrl: 'http://emb.local/v1', model: '', apiKey: '' }),
    ).toContain('模型名')
  })
})

describe('clearing a card', () => {
  it('sends null for exactly that kind', () => {
    expect(clearPatch('embedding')).toEqual({ embedding: null })
    expect(clearPatch('rerank')).toEqual({ rerank: null })
  })
})

describe('building a test', () => {
  it('sends only what was typed', () => {
    expect(testPayload({ baseUrl: 'http://emb.local/v1', model: '', apiKey: '' })).toEqual({
      base_url: 'http://emb.local/v1',
    })
    expect(testPayload({ baseUrl: '', model: 'emb-1', apiKey: 'sk-1' })).toEqual({
      model: 'emb-1',
      api_key: 'sk-1',
    })
  })

  it('sends nothing when the card is blank, letting the stored config answer', () => {
    const draft: RetrievalDraft = { baseUrl: '   ', model: '', apiKey: '' }
    expect(testPayload(draft)).toEqual({})
  })
})

describe('the retrieval floors', () => {
  const draft: ThresholdDraft = thresholdsDraftFrom(SETTINGS)

  it('seeds the draft from the stored values', () => {
    expect(draft).toEqual({ memory_floor: '0.55', semantic_floor: '0.5', rerank_floor: '0.25' })
  })

  it('accepts a draft that parses inside the range', () => {
    expect(thresholdsError({ ...draft, memory_floor: ' 0.7 ' })).toBeNull()
  })

  it('refuses a blank box, an unparsable one, and one past the ceiling', () => {
    expect(thresholdsError({ ...draft, memory_floor: '   ' })).toBe('还有一个值没有填。')
    expect(thresholdsError({ ...draft, memory_floor: 'abc' })).toContain('记忆相关下限')
    expect(thresholdsError({ ...draft, rerank_floor: '1.5' })).toContain('重排相关下限')
  })

  it('sends only the boxes that differ from what the server has', () => {
    // Saving the stored values back would plant rows that make the panel say
    // 已改过, so an unchanged box stays out of the patch entirely.
    const patch = thresholdsPayload({ ...draft, memory_floor: '0.7' }, SETTINGS)
    expect(patch).toEqual({ thresholds: { memory_floor: 0.7 } })
  })

  it('sends an empty thresholds object when nothing changed', () => {
    expect(thresholdsPayload(draft, SETTINGS)).toEqual({ thresholds: {} })
  })

  it('restoring the default sends nulls for every floor', () => {
    expect(THRESHOLDS_DEFAULT_PATCH).toEqual({
      thresholds: { memory_floor: null, semantic_floor: null, rerank_floor: null },
    })
  })
})
