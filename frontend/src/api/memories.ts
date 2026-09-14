/** Memory and recycle-bin calls, named after what they do. */

import { api } from './client'
import type { Memory, MemoryUpdate, TrashItem } from './types'

export function listMemories(): Promise<Memory[]> {
  return api<Memory[]>('/api/memories')
}

/** Sends only what changed: an absent key keeps its stored value. */
export function updateMemory(memoryId: string, patch: MemoryUpdate): Promise<Memory> {
  return api<Memory>(`/api/memories/${memoryId}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })
}

/**
 * Soft-deletes: the row moves to the recycle bin, from where the memory
 * page's deleted section can restore it - or drop it for good.
 */
export function deleteMemory(memoryId: string): Promise<void> {
  return api<void>(`/api/memories/${memoryId}`, { method: 'DELETE' })
}

export function listTrash(): Promise<TrashItem[]> {
  return api<TrashItem[]>('/api/trash')
}

/** Puts a deleted item back the way it was; the entry leaves the bin. */
export function restoreTrashItem(trashId: string): Promise<unknown> {
  return api<unknown>(`/api/trash/${trashId}/restore`, { method: 'POST' })
}

/** Drops the bin entry, after which the memory can no longer be restored. */
export function discardTrashItem(trashId: string): Promise<void> {
  return api<void>(`/api/trash/${trashId}`, { method: 'DELETE' })
}
