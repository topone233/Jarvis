/**
 * The rules about model profiles that are not visible in the shapes.
 *
 * Kept here, and tested, because all of them are easy to get subtly wrong:
 *
 * 1. **A key that was not retyped must not be sent at all.** The API never hands
 *    the key back - it lives in the OS credential manager - so the field is empty
 *    when an existing profile is opened. `PATCH` reads an explicit `api_key: ""`
 *    as *delete the stored key* (`backend/app/main.py` maps a falsy value to
 *    `secrets.delete`), so an empty box has to mean "leave it alone", which means
 *    leaving the field out of the request entirely. Sending the empty string
 *    would wipe the key of a profile the user only wanted to rename.
 *
 * 2. **Which profile is in effect when nothing is flagged.** Deleting the default
 *    is allowed and nothing promotes a successor, so "no default" is a state the
 *    screen has to be able to describe. The backend does not leave the app
 *    without a model either: `Store.get_default_model_profile` falls back to the
 *    oldest profile (`backend/app/store.py`). Same rule here, so the screen and
 *    the run agree about which configuration the next message will use.
 *
 * 3. **An unset generation limit is not a number.** `max_tokens` empty means the
 *    field is left out of the provider request so the endpoint applies its own
 *    limit - which is a different thing from asking for a very large one, and
 *    the only difference between the two on screen is an empty box.
 *
 * The call parameters are held as text, not as numbers. A half-typed number is
 * not a number yet, and `Number('')` being 0 would turn a cleared box into a
 * limit of zero. `profileValues` is where the text becomes values or a
 * complaint, and it runs before anything is sent.
 *
 * 4. **Thinking off is the endpoint's dialect, not ours.** What a provider wants
 *    to hear when it is not to think has no single answer, so the profile holds
 *    a JSON object that is merged into the request body when the composer's
 *    dial is at 关. Nothing on this side reads its keys, which is why it is
 *    edited as text and parsed back with no schema - see
 *    `backend/app/provider.py`. The dial's other three stops are not here: they
 *    send `reasoning_effort`, a name this app sets itself.
 */

import type { ModelProfile, ModelProfileInput } from './types'

/** The bounds `backend/app/schemas.py` enforces, so the form can say so first. */
export const LIMITS = {
  contextWindow: { min: 4_096, max: 2_000_000 },
  outputTokenReserve: { min: 256, max: 200_000 },
  maxTokens: { min: 1, max: 1_000_000 },
  compactPercent: { min: 10, max: 95 },
} as const

/** What `schemas.py` uses when a profile is created without them. */
export const DEFAULTS = {
  contextWindow: '128000',
  outputTokenReserve: '8192',
  maxTokens: '',
  compactPercent: '72',
  /** Empty means the profile says nothing about thinking either way. */
  thinkingOn: '',
  thinkingOff: '',
} as const

export interface ProfileDraft {
  /** The profile being edited, or null while adding a new one. */
  id: string | null
  name: string
  baseUrl: string
  chatModel: string
  /**
   * Never filled in from the server, because the server does not know how to
   * tell anyone what it is. Empty means "leave whatever is stored alone".
   */
  apiKey: string
  contextWindow: string
  outputTokenReserve: string
  /** Empty means no limit is sent at all. */
  maxTokens: string
  compactPercent: string
  /**
   * Both are JSON, kept as text for the same reason the numbers are.
   *
   * `thinkingOn` has no box on the form any more. The composer's dial sends
   * `reasoning_effort` for its three strengths and never reads this fragment:
   * it is carried here, and sent back unchanged, only so that saving a profile
   * does not throw away what was once typed into it. Nothing reads it, so give
   * it a control back only if the strengths become configurable again.
   */
  thinkingOn: string
  thinkingOff: string
}

/** Everything one profile holds that the send path has to turn into values. */
export interface ProfileValues {
  context_window: number
  output_token_reserve: number
  max_tokens: number | null
  compact_percent: number
  thinking_on: Record<string, unknown>
  thinking_off: Record<string, unknown>
}

export function emptyDraft(): ProfileDraft {
  return {
    id: null,
    name: '',
    baseUrl: '',
    chatModel: '',
    apiKey: '',
    ...DEFAULTS,
  }
}

/** Opens an existing profile in the form, without its key - see the note above. */
export function draftFrom(profile: ModelProfile): ProfileDraft {
  return {
    id: profile.id,
    name: profile.name,
    baseUrl: profile.base_url,
    chatModel: profile.chat_model,
    apiKey: '',
    contextWindow: String(profile.context_window),
    outputTokenReserve: String(profile.output_token_reserve),
    maxTokens: profile.max_tokens === null ? '' : String(profile.max_tokens),
    compactPercent: String(profile.compact_percent),
    thinkingOn: fragmentText(profile.thinking_on),
    thinkingOff: fragmentText(profile.thinking_off),
  }
}

/**
 * A fragment as the text box shows it.
 *
 * Indented, because the whole point of editing it as text is that a person can
 * read what they wrote; an empty object is shown as empty, so the common case
 * is a blank box rather than `{}` waiting to be deleted.
 */
function fragmentText(fragment: Record<string, unknown>): string {
  return Object.keys(fragment).length === 0 ? '' : JSON.stringify(fragment, null, 2)
}

/** A fragment as the request needs it, or the first thing wrong with it. */
function parseFragment(text: string, label: string): Record<string, unknown> | string {
  const trimmed = text.trim()
  if (trimmed === '') {
    return {}
  }
  let value: unknown
  try {
    value = JSON.parse(trimmed)
  } catch {
    value = undefined
  }
  // Arrays, null and half-typed JSON all land here. The first two parse fine and
  // are still not objects; the provider would take either as a body field and do
  // something surprising with it.
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    return `${label}要是一个 JSON 对象，比如 {"enable_thinking": false}。`
  }
  return value as Record<string, unknown>
}

/** A whole number, or null when the box does not hold one. */
function wholeNumber(text: string): number | null {
  const trimmed = text.trim()
  return /^\d+$/.test(trimmed) ? Number(trimmed) : null
}

function within(value: number, bounds: { min: number; max: number }): boolean {
  return value >= bounds.min && value <= bounds.max
}

/**
 * The call parameters and thinking fragments the form is holding, or the first
 * thing wrong with them.
 *
 * Bounds are checked here rather than left to the server because a 422 comes
 * back as "请求内容不符合要求。" and names no field - the form knows which box it
 * is, and the user is looking straight at it.
 */
export function profileValues(draft: ProfileDraft): ProfileValues | string {
  const contextWindow = wholeNumber(draft.contextWindow)
  if (contextWindow === null || !within(contextWindow, LIMITS.contextWindow)) {
    return `上下文大小要填 ${LIMITS.contextWindow.min} 到 ${LIMITS.contextWindow.max} 之间的整数。`
  }
  const reserve = wholeNumber(draft.outputTokenReserve)
  if (reserve === null || !within(reserve, LIMITS.outputTokenReserve)) {
    return `输出预留要填 ${LIMITS.outputTokenReserve.min} 到 ${LIMITS.outputTokenReserve.max} 之间的整数。`
  }
  if (reserve >= contextWindow) {
    // The input budget is the difference, so this leaves nothing to send.
    return '输出预留要小于上下文大小，两者之差才是能装下的输入。'
  }
  const maxText = draft.maxTokens.trim()
  const maxTokens = maxText === '' ? null : wholeNumber(maxText)
  if (maxTokens !== null && !within(maxTokens, LIMITS.maxTokens)) {
    return `生成上限要留空，或填 ${LIMITS.maxTokens.min} 到 ${LIMITS.maxTokens.max} 之间的整数。`
  }
  if (maxText !== '' && maxTokens === null) {
    return '生成上限要留空，或填一个整数。'
  }
  const percent = wholeNumber(draft.compactPercent)
  if (percent === null || !within(percent, LIMITS.compactPercent)) {
    return `压缩阈值要填 ${LIMITS.compactPercent.min} 到 ${LIMITS.compactPercent.max} 之间的整数。`
  }
  const thinkingOn = parseFragment(draft.thinkingOn, '「思考开」时要加的字段')
  if (typeof thinkingOn === 'string') {
    return thinkingOn
  }
  const thinkingOff = parseFragment(draft.thinkingOff, '「思考关」时要加的字段')
  if (typeof thinkingOff === 'string') {
    return thinkingOff
  }
  return {
    context_window: contextWindow,
    output_token_reserve: reserve,
    max_tokens: maxTokens,
    compact_percent: percent,
    thinking_on: thinkingOn,
    thinking_off: thinkingOff,
  }
}

/**
 * The model names out of a `/models` response.
 *
 * The list is the provider's own and is passed through untouched, so nothing
 * about it is guaranteed. An entry without a usable `id` is dropped rather than
 * rendered as an empty row in the dropdown.
 */
export function modelNames(models: unknown[]): string[] {
  const names: string[] = []
  for (const model of models) {
    const id = (model as { id?: unknown } | null)?.id
    if (typeof id === 'string' && id !== '' && !names.includes(id)) {
      names.push(id)
    }
  }
  return names
}

/**
 * How many tokens the threshold works out to, for the hint under the field.
 *
 * Mirrors `ContextManager.maybe_compact` in `backend/app/context.py`, floor
 * included: the number on screen is meant to be the number that will be used,
 * not an estimate of it. Null while the boxes do not hold numbers yet.
 */
export function compactAt(draft: ProfileDraft): number | null {
  const window_ = wholeNumber(draft.contextWindow)
  const reserve = wholeNumber(draft.outputTokenReserve)
  const percent = wholeNumber(draft.compactPercent)
  if (window_ === null || reserve === null || percent === null) {
    return null
  }
  const budget = window_ - reserve
  if (budget <= 0) {
    return null
  }
  return Math.max(2_000, Math.floor((budget * percent) / 100))
}

/**
 * The body of a new profile.
 *
 * The first one is made the default because there is nothing else it could be;
 * after that only the user's own choice changes which one is default, so a
 * profile added later never takes that away from the one already in use.
 */
export function createPayload(
  draft: ProfileDraft,
  isFirst: boolean,
  values: ProfileValues,
): ModelProfileInput {
  const apiKey = draft.apiKey.trim()
  return {
    name: draft.name.trim(),
    base_url: draft.baseUrl.trim(),
    chat_model: draft.chatModel.trim(),
    // Nothing is stored yet, so there is nothing to preserve: null is honest.
    api_key: apiKey === '' ? null : apiKey,
    ...values,
    ...(isFirst ? { is_default: true } : {}),
  }
}

/** The body of an edit. The key appears only when it was actually retyped. */
export function updatePayload(
  draft: ProfileDraft,
  values: ProfileValues,
): Partial<ModelProfileInput> {
  const payload: Partial<ModelProfileInput> = {
    name: draft.name.trim(),
    base_url: draft.baseUrl.trim(),
    chat_model: draft.chatModel.trim(),
    ...values,
  }
  const apiKey = draft.apiKey.trim()
  if (apiKey !== '') {
    payload.api_key = apiKey
  }
  return payload
}

/**
 * The profile a run would use with nothing pinned to the conversation.
 *
 * `created_at` is an ISO timestamp, so comparing the strings compares the times;
 * they carry microseconds, so two profiles never share one. No list order is
 * assumed: the server sorts by name, the fallback by age.
 */
export function effectiveDefault(profiles: ModelProfile[]): ModelProfile | null {
  const flagged = profiles.find((profile) => profile.is_default)
  if (flagged !== undefined) {
    return flagged
  }
  let oldest: ModelProfile | null = null
  for (const profile of profiles) {
    if (oldest === null || profile.created_at < oldest.created_at) {
      oldest = profile
    }
  }
  return oldest
}
