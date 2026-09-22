/**
 * The rules about the retrieval settings that are not visible in the shapes.
 *
 * 1. **A key that was not retyped must not be sent at all.** Same deal as the
 *    profiles (`api/profiles.ts`): the API never hands the key back, the box
 *    opens empty, and an empty string on the wire means *delete the stored
 *    key*. So a blank box is left out of the request entirely.
 *
 * 2. **Each card saves alone.** The PUT takes both kinds, but a kind left out
 *    of the request is untouched - which is what lets the 嵌入模型 card save
 *    without stomping whatever is stored for rerank, and vice versa.
 *
 * 3. **Testing does not require saving.** The test endpoints take the form's
 *    fields over what is stored, field by field: a fresh config can be tested
 *    before its first save, and a saved one re-tested bare, because an absent
 *    api_key makes the server use the stored one.
 */

import { api } from './client'
import type {
  RetrievalModelSetting,
  RetrievalSettings,
  RetrievalSettingsPatch,
  RetrievalTestResult,
} from './types'

export type RetrievalKind = 'embedding' | 'rerank'

export function fetchRetrievalSettings(): Promise<RetrievalSettings> {
  return api<RetrievalSettings>('/api/retrieval-settings')
}

export function saveRetrievalSettings(patch: RetrievalSettingsPatch): Promise<RetrievalSettings> {
  return api<RetrievalSettings>('/api/retrieval-settings', {
    method: 'PUT',
    body: JSON.stringify(patch),
  })
}

/** What one card's boxes hold. The key is never filled in from the server. */
export interface RetrievalDraft {
  baseUrl: string
  model: string
  apiKey: string
}

export function draftFromSetting(setting: RetrievalModelSetting | null): RetrievalDraft {
  return {
    baseUrl: setting?.base_url ?? '',
    model: setting?.model ?? '',
    apiKey: '',
  }
}

/**
 * One card's PUT body, or the first thing wrong with the boxes.
 *
 * A blank key stays out of the request (rule 1); the endpoint boxes are
 * checked here rather than left to the server's 422 for the same reason the
 * profile form does it - the user is looking straight at the box.
 */
export function savePatch(
  kind: RetrievalKind,
  draft: RetrievalDraft,
): RetrievalSettingsPatch | string {
  const baseUrl = draft.baseUrl.trim()
  const model = draft.model.trim()
  if (baseUrl === '') {
    return '接口地址不能为空。'
  }
  if (model === '') {
    return '模型名不能为空。'
  }
  const spec = { base_url: baseUrl, model }
  const apiKey = draft.apiKey.trim()
  // Branching rather than a computed key: the two kinds are different
  // properties, and the patch type says exactly that.
  return kind === 'embedding'
    ? { embedding: apiKey === '' ? spec : { ...spec, api_key: apiKey } }
    : { rerank: apiKey === '' ? spec : { ...spec, api_key: apiKey } }
}

/** The patch that un-configures one kind; the server deletes its key with it. */
export function clearPatch(kind: RetrievalKind): RetrievalSettingsPatch {
  return kind === 'embedding' ? { embedding: null } : { rerank: null }
}

/** The test endpoint's body: only what was typed; the server fills the rest. */
export function testPayload(draft: RetrievalDraft): Record<string, string> {
  const payload: Record<string, string> = {}
  const baseUrl = draft.baseUrl.trim()
  const model = draft.model.trim()
  const apiKey = draft.apiKey.trim()
  if (baseUrl !== '') payload.base_url = baseUrl
  if (model !== '') payload.model = model
  if (apiKey !== '') payload.api_key = apiKey
  return payload
}

export function testEmbedding(draft: RetrievalDraft): Promise<RetrievalTestResult> {
  return api<RetrievalTestResult>('/api/retrieval-settings/embedding/test', {
    method: 'POST',
    body: JSON.stringify(testPayload(draft)),
  })
}

export function testRerank(draft: RetrievalDraft): Promise<RetrievalTestResult> {
  return api<RetrievalTestResult>('/api/retrieval-settings/rerank/test', {
    method: 'POST',
    body: JSON.stringify(testPayload(draft)),
  })
}

/**
 * The retrieval floors' labels and caps, mirrored from the API's schema
 * (`RetrievalThresholdsUpdate`). Like the tool-calls tab, the draft is the
 * raw text of the inputs - a half-typed value is a draft state, and 保存
 * parses with the first problem becoming an inline sentence.
 */
export const RETRIEVAL_THRESHOLDS = {
  memory_floor: { label: '记忆相关下限', min: 0, max: 0.99 },
  semantic_floor: { label: '知识语义下限', min: 0, max: 0.99 },
  rerank_floor: { label: '重排相关下限', min: 0, max: 0.99 },
} as const

export type ThresholdKey = keyof typeof RETRIEVAL_THRESHOLDS

/** One input's draft: what the box shows, which may be half-typed. */
export type ThresholdDraft = Record<ThresholdKey, string>

export function thresholdsDraftFrom(settings: RetrievalSettings): ThresholdDraft {
  return {
    memory_floor: String(settings.thresholds.memory_floor.value),
    semantic_floor: String(settings.thresholds.semantic_floor.value),
    rerank_floor: String(settings.thresholds.rerank_floor.value),
  }
}

/** The first problem in the draft, or null when it would save. */
export function thresholdsError(draft: ThresholdDraft): string | null {
  for (const key of Object.keys(RETRIEVAL_THRESHOLDS) as ThresholdKey[]) {
    const text = draft[key].trim()
    if (text === '') {
      return '还有一个值没有填。'
    }
    const value = Number(text)
    if (
      !Number.isFinite(value) ||
      value < RETRIEVAL_THRESHOLDS[key].min ||
      value > RETRIEVAL_THRESHOLDS[key].max
    ) {
      return `${RETRIEVAL_THRESHOLDS[key].label}要在 0 到 0.99 之间。`
    }
  }
  return null
}

/**
 * The draft as a PUT body - only what differs from what the server has, so
 * saving the default value back does not plant a row that makes the panel
 * say 已改过.
 */
export function thresholdsPayload(
  draft: ThresholdDraft,
  settings: RetrievalSettings,
): RetrievalSettingsPatch {
  const thresholds: NonNullable<RetrievalSettingsPatch['thresholds']> = {}
  for (const key of Object.keys(RETRIEVAL_THRESHOLDS) as ThresholdKey[]) {
    const value = Number(draft[key].trim())
    if (value !== settings.thresholds[key].value) {
      thresholds[key] = value
    }
  }
  return { thresholds }
}

/** 恢复默认's body: nulls, which delete the rows. */
export const THRESHOLDS_DEFAULT_PATCH: RetrievalSettingsPatch = {
  thresholds: { memory_floor: null, semantic_floor: null, rerank_floor: null },
}
