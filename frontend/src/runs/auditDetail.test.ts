import { describe, expect, it } from 'vitest'

import { detailLines, summaryOf } from './auditDetail'
import type { AuditRow } from './reducer'

function row(stage: string, state: string, payload: Record<string, unknown>): AuditRow {
  return {
    stage,
    state,
    sequence: 1,
    payload,
    startedAt: '2026-09-14T00:00:00.000Z',
    endedAt: state === 'running' ? null : '2026-09-14T00:00:01.000Z',
  }
}

describe('detailLines', () => {
  it('says what the memory step did, one line per action', () => {
    const lines = detailLines(
      row('memory_write', 'completed', {
        count: 2,
        items: [
          {
            action: 'created',
            memory: { kind: 'preference', memory_key: '回复风格', content: '喜欢简洁回答' },
          },
          {
            action: 'forgotten',
            memory: { kind: 'fact', memory_key: '主题', content: '喜欢深色主题' },
            count: 1,
          },
        ],
      }),
    )
    expect(lines).toEqual([
      '新增 · [偏好] 回复风格：喜欢简洁回答',
      '忘记 · [事实] 主题：喜欢深色主题',
    ])
  })

  it('notes when a forget took more than one memory with it', () => {
    const lines = detailLines(
      row('memory_write', 'completed', {
        count: 1,
        items: [
          {
            action: 'forgotten',
            memory: { kind: 'fact', memory_key: '主题', content: '旧内容' },
            count: 2,
          },
        ],
      }),
    )
    expect(lines[0]).toContain('（连同同键的共 2 条）')
  })

  it('names the model that actually answered, and its tokens', () => {
    const lines = detailLines(
      row('model_stream', 'completed', {
        model: 'deepseek-chat',
        usage: { prompt_tokens: 120, completion_tokens: 45 },
      }),
    )
    expect(lines).toEqual(['这一轮用的模型：deepseek-chat', 'token：输入 120，输出 45'])
  })

  it('reports what retrieval assembled, down to what it found nothing of', () => {
    const lines = detailLines(
      row('context_retrieval', 'completed', {
        input_token_estimate: 1234,
        remaining_token_estimate: 5678,
        memory_count: 2,
        memory_keys: ['回复风格', '主题'],
        citation_count: 0,
      }),
    )
    expect(lines).toEqual([
      '上下文约 1234 tokens，窗口剩 5678',
      '注入记忆 2 条：回复风格、主题',
      '召回知识 0 条',
    ])
  })

  it('reports the range a compaction folded', () => {
    const lines = detailLines(
      row('context_compaction', 'completed', { compacted: true, range: [1, 6] }),
    )
    expect(lines).toEqual(['把第 1 到 6 条消息折叠成了一份摘要。'])
  })

  it('stays empty for stages and payloads with nothing to say', () => {
    expect(detailLines(row('context_compaction', 'completed', { compacted: false }))).toEqual([])
    expect(detailLines(row('model_stream', 'running', {}))).toEqual([])
    expect(detailLines(row('something_else', 'completed', { count: 1 }))).toEqual([])
  })
})

describe('summaryOf', () => {
  it('counts memory actions', () => {
    expect(summaryOf(row('memory_write', 'completed', { count: 2 }))).toBe('2 条')
  })

  it('leaves an untriggered compaction wordless', () => {
    // Whether a stage has anything to say is the arrow's job; the summary
    // line only ever carries a count.
    expect(summaryOf(row('context_compaction', 'completed', { compacted: false }))).toBe('')
  })

  it('keeps the failure note first', () => {
    expect(summaryOf(row('model_stream', 'failed', { error: '超时' }))).toBe('失败')
  })

  it('is empty for a plain completed stage', () => {
    expect(summaryOf(row('model_stream', 'completed', { usage: {} }))).toBe('')
  })
})
