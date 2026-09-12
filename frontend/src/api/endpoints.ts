/** Every Core API call the chat area makes, named after what it does. */

import { api } from './client'
import type { Conversation, Message, ModelProfile, Run, RunEventRecord } from './types'

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

export function getRun(runId: string): Promise<Run> {
  return api<Run>(`/api/runs/${runId}`)
}

export function listRunEvents(runId: string): Promise<RunEventRecord[]> {
  return api<RunEventRecord[]>(`/api/runs/${runId}/events`)
}

export function cancelRun(runId: string): Promise<Run> {
  return api<Run>(`/api/runs/${runId}/cancel`, { method: 'POST' })
}

export function listModelProfiles(): Promise<ModelProfile[]> {
  return api<ModelProfile[]>('/api/model-profiles')
}

export interface ModelProfileInput {
  name: string
  base_url: string
  chat_model: string
  embedding_model?: string | null
  api_key?: string | null
  is_default?: boolean
}

export function createModelProfile(input: ModelProfileInput): Promise<ModelProfile> {
  return api<ModelProfile>('/api/model-profiles', {
    method: 'POST',
    body: JSON.stringify(input),
  })
}

export function testModelProfile(profileId: string): Promise<{ ok: boolean; models: unknown[] }> {
  return api<{ ok: boolean; models: unknown[] }>(`/api/model-profiles/${profileId}/test`, {
    method: 'POST',
  })
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

export function sendFeedback(messageId: string, kind: 'up' | 'down'): Promise<unknown> {
  return api<unknown>(`/api/messages/${messageId}/feedback`, {
    method: 'POST',
    body: JSON.stringify({ kind }),
  })
}
