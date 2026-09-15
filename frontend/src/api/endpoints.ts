/** Every Core API call the chat area makes, named after what it does. */

import { api } from './client'
import type {
  AppSettings,
  Conversation,
  Message,
  ModelProfile,
  ModelProfileInput,
  Run,
  RunEventRecord,
  SettingsPatch,
} from './types'

export function listConversations(projectId?: string): Promise<Conversation[]> {
  const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''
  return api<Conversation[]>(`/api/conversations${query}`)
}

export function createConversation(title = '新对话'): Promise<Conversation> {
  return api<Conversation>('/api/conversations', {
    method: 'POST',
    body: JSON.stringify({ title }),
  })
}

export function deleteConversation(conversationId: string): Promise<void> {
  return api<void>(`/api/conversations/${conversationId}`, { method: 'DELETE' })
}

export function renameConversation(conversationId: string, title: string): Promise<Conversation> {
  return api<Conversation>(`/api/conversations/${conversationId}`, {
    method: 'PATCH',
    body: JSON.stringify({ title }),
  })
}

export function listMessages(conversationId: string): Promise<Message[]> {
  return api<Message[]>(`/api/conversations/${conversationId}/messages`)
}

/** A stored pasted image, by its position in the message. */
export function messageImageUrl(conversationId: string, messageId: string, index: number): string {
  return `/api/conversations/${conversationId}/messages/${messageId}/images/${index}`
}

export function getRun(runId: string): Promise<Run> {
  return api<Run>(`/api/runs/${runId}`)
}

export function listRunEvents(runId: string): Promise<RunEventRecord[]> {
  return api<RunEventRecord[]>(`/api/runs/${runId}/events`)
}

/**
 * The audit trail of every run in a conversation, keyed by run id.
 *
 * One request rather than one per answer, because opening a conversation means
 * drawing the steps of everything in it - not just the newest answer, which is
 * the only one the stream will ever say anything about.
 */
export function listConversationRunEvents(
  conversationId: string,
): Promise<Record<string, RunEventRecord[]>> {
  return api<Record<string, RunEventRecord[]>>(`/api/conversations/${conversationId}/run-events`)
}

export function cancelRun(runId: string): Promise<Run> {
  return api<Run>(`/api/runs/${runId}/cancel`, { method: 'POST' })
}

export function listModelProfiles(): Promise<ModelProfile[]> {
  return api<ModelProfile[]>('/api/model-profiles')
}

export function createModelProfile(input: ModelProfileInput): Promise<ModelProfile> {
  return api<ModelProfile>('/api/model-profiles', {
    method: 'POST',
    body: JSON.stringify(input),
  })
}

/** Sends only what changed: an absent field keeps its stored value. */
export function updateModelProfile(
  profileId: string,
  input: Partial<ModelProfileInput>,
): Promise<ModelProfile> {
  return api<ModelProfile>(`/api/model-profiles/${profileId}`, {
    method: 'PATCH',
    body: JSON.stringify(input),
  })
}

/** Moves the profile to the recycle bin; the credential manager entry goes too. */
export function deleteModelProfile(profileId: string): Promise<void> {
  return api<void>(`/api/model-profiles/${profileId}`, { method: 'DELETE' })
}

export function testModelProfile(profileId: string): Promise<{ ok: boolean; models: unknown[] }> {
  return api<{ ok: boolean; models: unknown[] }>(`/api/model-profiles/${profileId}/test`, {
    method: 'POST',
  })
}

/**
 * The model names this profile's endpoint offers, for the composer's dropdown.
 *
 * Read from the service rather than stored, and returned as the service sent
 * it: `modelNames` in `profiles.ts` is where the list becomes names.
 */
export function listProfileModels(profileId: string): Promise<{ models: unknown[] }> {
  return api<{ models: unknown[] }>(`/api/model-profiles/${profileId}/models`)
}

export function setupDataDirectory(dataDirectory: string): Promise<{
  configured: boolean
  data_directory: string
}> {
  return api<{ configured: boolean; data_directory: string }>('/api/setup', {
    method: 'POST',
    body: JSON.stringify({ data_directory: dataDirectory }),
  })
}

export function getSettings(): Promise<AppSettings> {
  return api<AppSettings>('/api/settings')
}

/**
 * Saves prompts (and the quick-prompt list), and returns the whole screen's
 * state afterwards.
 *
 * A key left out is untouched; a key sent as null goes back to the text the
 * code ships. The response is the same shape as the read, so the screen can
 * redraw from it without a second request.
 */
export function saveSettings(patch: SettingsPatch): Promise<AppSettings> {
  return api<AppSettings>('/api/settings', { method: 'PUT', body: JSON.stringify(patch) })
}

export function sendFeedback(messageId: string, kind: 'up' | 'down'): Promise<unknown> {
  return api<unknown>(`/api/messages/${messageId}/feedback`, {
    method: 'POST',
    body: JSON.stringify({ kind }),
  })
}
