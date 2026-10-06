/**
 * 便签的前端 API：全部走宿主给本插件挂的前缀 /api/plugins/notepad。
 */

import { api } from '../../../frontend/src/api/client'

const BASE = '/api/plugins/notepad'

export interface NoteSummary {
  id: string
  title: string
  excerpt: string
  tags: string[]
  source: string
  tag_status: 'none' | 'pending' | 'done' | 'failed'
  created_at: string
  updated_at: string
}

export type NoteFull = NoteSummary & { content: string }

export interface TagCount {
  tag: string
  count: number
}

export function listNotes(
  options: { q?: string; tag?: string; limit?: number; offset?: number } = {},
): Promise<{ items: NoteSummary[] }> {
  const params = new URLSearchParams()
  if (options.q) params.set('q', options.q)
  if (options.tag) params.set('tag', options.tag)
  if (options.limit !== undefined) params.set('limit', String(options.limit))
  if (options.offset !== undefined) params.set('offset', String(options.offset))
  const query = params.size > 0 ? `?${params.toString()}` : ''
  return api<{ items: NoteSummary[] }>(`${BASE}/notes${query}`)
}

export function createNote(input: {
  title: string
  content: string
  source: 'popup' | 'page'
}): Promise<NoteFull> {
  return api<NoteFull>(`${BASE}/notes`, { method: 'POST', body: JSON.stringify(input) })
}

export function getNote(noteId: string): Promise<NoteFull> {
  return api<NoteFull>(`${BASE}/notes/${noteId}`)
}

export function updateNote(
  noteId: string,
  patch: {
    title?: string
    content?: string
    tags?: string[]
    base_updated_at?: string
  },
): Promise<NoteFull> {
  return api<NoteFull>(`${BASE}/notes/${noteId}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })
}

export function deleteNote(noteId: string): Promise<void> {
  return api<void>(`${BASE}/notes/${noteId}`, { method: 'DELETE' })
}

export function listDeletedNotes(): Promise<{ items: NoteSummary[] }> {
  return api<{ items: NoteSummary[] }>(`${BASE}/notes/deleted`)
}

export function restoreNote(noteId: string): Promise<{ note: NoteSummary }> {
  return api<{ note: NoteSummary }>(`${BASE}/notes/${noteId}/restore`, { method: 'POST' })
}

export function purgeNote(noteId: string): Promise<void> {
  return api<void>(`${BASE}/notes/${noteId}/purge`, { method: 'DELETE' })
}

export function listTags(): Promise<{ items: TagCount[] }> {
  return api<{ items: TagCount[] }>(`${BASE}/tags`)
}

export function retagNote(noteId: string): Promise<NoteFull> {
  return api<NoteFull>(`${BASE}/notes/${noteId}/retag`, { method: 'POST' })
}

/** The plugin's own config, read through the host's listing. */
export function readPluginConfig(): Promise<Record<string, string | number | boolean>> {
  return api<Array<{ id: string; config: Record<string, string | number | boolean> }>>(
    '/api/plugins',
  ).then((plugins) => plugins.find((plugin) => plugin.id === 'notepad')?.config ?? {})
}
