/** Knowledge base calls: import, list, search, delete - named after what they do. */

import { api } from './client'
import type { Citation, KnowledgeContent, KnowledgeDocument, KnowledgeImportResult } from './types'

export function listKnowledgeDocuments(): Promise<KnowledgeDocument[]> {
  return api<KnowledgeDocument[]>('/api/knowledge/documents')
}

/**
 * The files' bytes go in a multipart body, and the browser owns the
 * Content-Type - `withJsonBody` leaves FormData alone for exactly this call.
 */
export function importKnowledge(files: File[]): Promise<{ items: KnowledgeImportResult[] }> {
  const body = new FormData()
  for (const file of files) {
    body.append('files', file)
  }
  return api<{ items: KnowledgeImportResult[] }>('/api/knowledge/import', {
    method: 'POST',
    body,
  })
}

/** Soft-deletes: the document moves to the recycle bin, restorable from the page. */
export function deleteKnowledgeDocument(documentId: string): Promise<void> {
  return api<void>(`/api/knowledge/documents/${documentId}`, { method: 'DELETE' })
}

/** The same search a run does, without going through a run. */
export function searchKnowledge(query: string): Promise<{ items: Citation[] }> {
  return api<{ items: Citation[] }>(`/api/knowledge/search?query=${encodeURIComponent(query)}`)
}

/** The canonical text behind a citation, for the 查看原文 panel. */
export function fetchKnowledgeContent(documentId: string): Promise<KnowledgeContent> {
  return api<KnowledgeContent>(`/api/knowledge/documents/${documentId}/content`)
}

/** The extensions the file picker offers, in step with the backend's two sets. */
export const KNOWLEDGE_ACCEPT = [
  '.docx',
  '.xlsx',
  '.pptx',
  '.pdf',
  '.md',
  '.markdown',
  '.txt',
  '.csv',
  '.html',
  '.json',
  '.xml',
  '.yaml',
  '.yml',
  '.bat',
  '.c',
  '.cc',
  '.cpp',
  '.cs',
  '.css',
  '.go',
  '.h',
  '.hpp',
  '.ini',
  '.java',
  '.js',
  '.jsx',
  '.kt',
  '.kts',
  '.php',
  '.ps1',
  '.py',
  '.rb',
  '.rs',
  '.sh',
  '.sql',
  '.swift',
  '.toml',
  '.ts',
  '.tsx',
  '.vue',
].join(',')

/**
 * One row per file. A semantic-index failure arrives as its own `warning`
 * entry before the finished one; the page wants the pair as a single line
 * that keeps the warning's reason.
 */
export function summarizeImportResults(items: KnowledgeImportResult[]): KnowledgeImportResult[] {
  const rows = new Map<string, KnowledgeImportResult>()
  for (const item of items) {
    const existing = rows.get(item.filename)
    if (existing !== undefined && existing.status === 'warning') {
      rows.set(item.filename, { ...item, reason: existing.reason })
    } else {
      rows.set(item.filename, { ...item })
    }
  }
  return [...rows.values()]
}
