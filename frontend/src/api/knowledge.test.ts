import { describe, expect, it } from 'vitest'

import { KNOWLEDGE_ACCEPT, summarizeImportResults } from './knowledge'
import type { KnowledgeImportResult } from './types'

function result(over: Partial<KnowledgeImportResult>): KnowledgeImportResult {
  return { filename: '设计.docx', status: 'ready', ...over }
}

describe('summarizeImportResults', () => {
  it('keeps one row per file', () => {
    const rows = summarizeImportResults([
      result({ filename: 'a.md', status: 'ready' }),
      result({ filename: 'b.txt', status: 'ready' }),
    ])
    expect(rows.map((row) => row.filename)).toEqual(['a.md', 'b.txt'])
  })

  it('merges a warning into the finished entry, keeping the reason', () => {
    const rows = summarizeImportResults([
      result({
        status: 'warning',
        reason: '已完成关键词索引，语义索引暂不可用：嵌入服务请求失败。',
      }),
      result({ status: 'ready_without_embeddings' }),
    ])
    expect(rows).toHaveLength(1)
    expect(rows[0].status).toBe('ready_without_embeddings')
    expect(rows[0].reason).toContain('语义索引暂不可用')
  })

  it('leaves a skipped row and its reason alone', () => {
    const rows = summarizeImportResults([
      result({ filename: '旧.doc', status: 'skipped', reason: '旧版 Office 格式暂不支持，请在 Office 里另存为 .docx/.xlsx/.pptx 后再导入。' }),
    ])
    expect(rows).toHaveLength(1)
    expect(rows[0].status).toBe('skipped')
    expect(rows[0].reason).toContain('另存为')
  })
})

describe('KNOWLEDGE_ACCEPT', () => {
  it('offers the office formats and never the legacy ones', () => {
    const extensions = KNOWLEDGE_ACCEPT.split(',')
    for (const wanted of ['.docx', '.xlsx', '.pptx', '.pdf', '.md', '.txt']) {
      expect(extensions).toContain(wanted)
    }
    for (const legacy of ['.doc', '.xls', '.ppt']) {
      expect(extensions).not.toContain(legacy)
    }
  })
})
