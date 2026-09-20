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

const text = (t: string) => ({ kind: 'text', text: t })
const code = (label: string, t: string) => ({ kind: 'code', label, text: t })

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
      text('新增 · [偏好] 回复风格：喜欢简洁回答'),
      text('忘记 · [事实] 主题：喜欢深色主题'),
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
    expect(lines[0]).toEqual(text('忘记 · [事实] 主题：旧内容（连同同键的共 2 条）'))
  })

  it('shows a memory call as raw JSON plus the result the model read', () => {
    const lines = detailLines(
      row('memory_write', 'completed', {
        call: { id: 'call_1', name: 'save_memory', arguments: '{"key":"主题"}' },
        output: '已保存记忆：主题',
        count: 1,
        items: [
          {
            action: 'created',
            memory: { kind: 'fact', memory_key: '主题', content: '旧内容' },
          },
        ],
      }),
    )
    expect(lines).toEqual([
      code(
        'AI 原始调用',
        '{\n  "id": "call_1",\n  "name": "save_memory",\n  "arguments": "{\\"key\\":\\"主题\\"}"\n}',
      ),
      code('执行结果', '已保存记忆：主题'),
      text('新增 · [事实] 主题：旧内容'),
    ])
  })

  it('names the model that actually answered, and its tokens', () => {
    const lines = detailLines(
      row('model_stream', 'completed', {
        model: 'deepseek-chat',
        usage: { prompt_tokens: 120, completion_tokens: 45 },
      }),
    )
    expect(lines).toEqual([text('这一轮用的模型：deepseek-chat'), text('token：输入 120，输出 45')])
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
      text('上下文约 1234 tokens，窗口剩 5678'),
      text('注入记忆 2 条：回复风格、主题'),
      text('召回知识 0 条'),
    ])
  })

  it('reports the range a compaction folded', () => {
    const lines = detailLines(
      row('context_compaction', 'completed', { compacted: true, range: [1, 6] }),
    )
    expect(lines).toEqual([text('把第 1 到 6 条消息折叠成了一份摘要。')])
  })

  it('shows the knowledge command a round ran', () => {
    expect(
      detailLines(row('knowledge_tool', 'completed', { command: 'list', output_chars: 312 })),
    ).toEqual([text('knowledge list'), text('返回 312 字符')])
    expect(
      detailLines(row('knowledge_tool', 'running', { command: 'read cases --section 2.3' })),
    ).toEqual([text('knowledge read cases --section 2.3')])
  })

  it('shows a tool call as the AI’s raw JSON and the full result', () => {
    const lines = detailLines(
      row('knowledge_tool', 'completed', {
        command: 'list',
        call: { id: 'call_1', name: 'knowledge', arguments: '{"command":"list"}' },
        output: 'knowledge: 共 1 份文档',
        output_chars: 22,
      }),
    )
    expect(lines).toEqual([
      code(
        'AI 原始调用',
        '{\n  "id": "call_1",\n  "name": "knowledge",\n  "arguments": "{\\"command\\":\\"list\\"}"\n}',
      ),
      code('执行结果', 'knowledge: 共 1 份文档'),
    ])
  })

  it('draws a refused or unknown call from the generic tool_call stage', () => {
    const lines = detailLines(
      row('tool_call', 'failed', {
        call: { id: 'call_9', name: 'nope', arguments: '' },
        output: 'knowledge: unknown tool: nope。',
        reason: '未知工具',
      }),
    )
    expect(lines).toEqual([
      code('AI 原始调用', '{\n  "id": "call_9",\n  "name": "nope",\n  "arguments": ""\n}'),
      code('执行结果', 'knowledge: unknown tool: nope。'),
    ])
  })

  it('says why a run closed on the round budget', () => {
    expect(detailLines(row('tool_rounds_exhausted', 'completed', { rounds: 10 }))).toEqual([
      text('连续多轮调用工具后仍未给出回答，已按轮次上限收尾。'),
    ])
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

  it('tells tool rows apart by their command', () => {
    // Several rows share one stage label now; the command is the difference.
    expect(summaryOf(row('knowledge_tool', 'completed', { command: 'grep 登录' }))).toBe(
      'grep 登录',
    )
    expect(summaryOf(row('tool_call', 'failed', { reason: '重复命令已拦截' }))).toBe(
      '失败 · 重复命令已拦截',
    )
  })
})
