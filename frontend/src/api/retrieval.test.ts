import { describe, expect, it } from 'vitest'

import {
  clearPatch,
  draftFromSetting,
  savePatch,
  testPayload,
  type RetrievalDraft,
} from './retrieval'
import type { RetrievalModelSetting } from './types'

const EMBEDDING: RetrievalModelSetting = {
  base_url: 'http://emb.local/v1',
  model: 'emb-1',
  has_api_key: true,
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
