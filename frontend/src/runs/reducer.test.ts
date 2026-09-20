import { describe, expect, it } from 'vitest'

import type { Citation, RunEventRecord } from '../api/types'
import { stageMillis } from './duration'
import type { RunEvent } from './events'
import { createTurn, reduce, type TurnAction, type TurnState } from './reducer'

const MESSAGE_ID = 'msg_1'
const RAN_AT = '2026-09-13T06:57:14.000Z'
const DONE_AT = '2026-09-13T06:57:17.140Z'

function run(events: RunEvent[], start?: TurnState, messageId = MESSAGE_ID): TurnState {
  const initial = start ?? createTurn('local')
  return events.reduce<TurnState>(
    (state, event) => reduce(state, { type: 'event', event }),
    reduce(initial, { type: 'event', event: started(messageId) }),
  )
}

function started(messageId = MESSAGE_ID): RunEvent {
  return { type: 'run.started', runId: 'run_1', assistantMessageId: messageId }
}

function delta(text: string, messageId = MESSAGE_ID): RunEvent {
  return { type: 'message.delta', messageId, delta: text }
}

function audit(
  stage: string,
  state: string,
  sequence: number,
  createdAt = RAN_AT,
  payload: Record<string, unknown> = {},
): RunEvent {
  return {
    type: 'audit',
    record: {
      id: `e${sequence}`,
      run_id: 'run_1',
      sequence,
      stage,
      state,
      payload,
      created_at: createdAt,
    },
  }
}

function apply(state: TurnState, ...actions: TurnAction[]): TurnState {
  return actions.reduce(reduce, state)
}

describe('reduce', () => {
  it('appends deltas after the first', () => {
    const state = run([delta('你好'), delta('，'), delta('世界')])
    expect(state.content).toBe('你好，世界')
    expect(state.phase).toBe('streaming')
  })

  it('replaces the seeded text with the first delta of a connection', () => {
    // The seed comes from what is on disk; the server's first flush is the
    // whole accumulated text, which is always at least that much. Appending
    // here would duplicate the prefix.
    const seeded = createTurn('local', { runId: 'run_1', content: '已经落盘的部分' })
    const state = run([delta('已经落盘的部分，外加新的一段')], seeded)
    expect(state.content).toBe('已经落盘的部分，外加新的一段')
  })

  it('replaces again after a reconnect', () => {
    let state = run([delta('第一段')])
    state = apply(state, { type: 'detached' }, { type: 'attached' })
    state = run([delta('第一段第二段')], state)
    expect(state.content).toBe('第一段第二段')
    expect(state.detached).toBe(false)
  })

  it('does not duplicate when the terminal event repeats the accumulated text', () => {
    const state = run([
      delta('半'),
      delta('句'),
      { type: 'message.completed', messageId: MESSAGE_ID, content: '半句', metadata: {} },
    ])
    expect(state.content).toBe('半句')
    expect(state.phase).toBe('completed')
  })

  it('ignores text that arrives after a terminal event', () => {
    const state = run([
      delta('完整回答'),
      { type: 'message.completed', messageId: MESSAGE_ID, content: '完整回答', metadata: {} },
      delta('迟到的碎片'),
    ])
    expect(state.content).toBe('完整回答')
  })

  it('keeps accepting audits after the answer is complete', () => {
    // The memory write is reported once the text is already on screen, so
    // gating audits on the phase would drop the last row of the progress strip.
    const state = run([
      delta('回答'),
      { type: 'message.completed', messageId: MESSAGE_ID, content: '回答', metadata: {} },
      audit('memory_write', 'running', 5),
      audit('memory_write', 'completed', 6),
    ])
    expect(state.audits).toEqual([
      {
        stage: 'memory_write',
        state: 'completed',
        sequence: 5,
        payload: {},
        startedAt: RAN_AT,
        endedAt: RAN_AT,
      },
    ])
  })

  it('carries a stage’s start across to the record that ends it', () => {
    // The two records collapse into one row, and a duration is the distance
    // between them - so the start has to survive being overwritten, or the
    // seconds on screen would be the width of the last record alone.
    const state = run([
      audit('model_stream', 'running', 1, RAN_AT),
      audit('model_stream', 'completed', 2, DONE_AT),
    ])
    expect(state.audits).toEqual([
      {
        stage: 'model_stream',
        state: 'completed',
        sequence: 1,
        payload: {},
        startedAt: RAN_AT,
        endedAt: DONE_AT,
      },
    ])
    expect(stageMillis(state.audits[0], 0)).toBe(3140)
  })

  it('merges the payloads of the records that make up a stage', () => {
    // The model's name is announced when the stage starts, its token usage
    // when it ends. Replacing instead of merging would lose the name the
    // moment the answer finished - the row would say how many tokens it cost
    // but not with what model.
    const state = run([
      audit('model_stream', 'running', 1, RAN_AT, { model: 'deepseek-chat' }),
      audit('model_stream', 'completed', 2, DONE_AT, {
        usage: { prompt_tokens: 120, completion_tokens: 45 },
      }),
    ])
    expect(state.audits[0].payload).toEqual({
      model: 'deepseek-chat',
      usage: { prompt_tokens: 120, completion_tokens: 45 },
    })
  })

  it('times a stage that is still running against the clock', () => {
    const state = run([audit('model_stream', 'running', 1, RAN_AT)])
    expect(state.audits[0].endedAt).toBeNull()
    expect(stageMillis(state.audits[0], Date.parse(RAN_AT) + 2500)).toBe(2500)
  })

  it('collapses a stage that reports running then completed', () => {
    const state = run([
      audit('context_compaction', 'running', 1),
      audit('context_compaction', 'completed', 2),
      audit('context_retrieval', 'running', 3),
    ])
    expect(state.audits.map((row) => [row.stage, row.state])).toEqual([
      ['context_compaction', 'completed'],
      ['context_retrieval', 'running'],
    ])
  })

  it('keeps every occurrence of a stage that runs more than once', () => {
    // Each knowledge round is its own tool call; collapsing them would hide
    // every step but the last, which is exactly what an audit must not do.
    const state = run([
      audit('knowledge_tool', 'running', 3, RAN_AT, {
        command: 'list',
        call: { name: 'knowledge' },
      }),
      audit('knowledge_tool', 'completed', 4, DONE_AT, { command: 'list', output: '…' }),
      audit('knowledge_tool', 'running', 5, RAN_AT, { command: 'grep 登录' }),
      audit('knowledge_tool', 'completed', 6, DONE_AT, { command: 'grep 登录', output: '…' }),
    ])
    expect(state.audits.map((row) => [row.sequence, row.stage, row.state])).toEqual([
      [3, 'knowledge_tool', 'completed'],
      [5, 'knowledge_tool', 'completed'],
    ])
    expect(state.audits[0].payload).toEqual({
      command: 'list',
      call: { name: 'knowledge' },
      output: '…',
    })
    expect(state.audits[1].payload).toEqual({ command: 'grep 登录', output: '…' })
  })

  it('gives an ending record with no open row its own row', () => {
    // A `/name` skill step is announced completed, without a run-up - it
    // still has to show, or a whole action would be missing from the trail.
    const state = run([audit('skill_tool', 'completed', 2, DONE_AT, { trigger: 'user_request' })])
    expect(state.audits.map((row) => [row.stage, row.state, row.sequence])).toEqual([
      ['skill_tool', 'completed', 2],
    ])
    expect(state.audits[0].startedAt).toBeNull()
  })

  it('opens a new row when a stage starts again while one is open', () => {
    // A stage interrupted between records - a crash left a `running` open -
    // must not have its next occurrence merge into the corpse.
    const state = run([
      audit('knowledge_tool', 'running', 1, RAN_AT, { command: 'list' }),
      audit('knowledge_tool', 'running', 2, DONE_AT, { command: 'read x' }),
    ])
    expect(state.audits.map((row) => [row.sequence, row.state])).toEqual([
      [1, 'running'],
      [2, 'running'],
    ])
  })

  it('keeps the partial text when a run fails', () => {
    const state = run([delta('写到一半'), { type: 'run.failed', error: '连接被中断' }])
    expect(state.content).toBe('写到一半')
    expect(state.phase).toBe('failed')
    expect(state.error).toBe('连接被中断')
  })

  it('takes the full text from a cancelled run', () => {
    const state = run([
      delta('半句'),
      { type: 'run.cancelled', messageId: MESSAGE_ID, content: '半句' },
    ])
    expect(state.phase).toBe('cancelled')
    expect(state.content).toBe('半句')
  })

  it('reads reasoning from the terminal metadata when no deltas arrived', () => {
    const state = run([
      {
        type: 'message.completed',
        messageId: MESSAGE_ID,
        content: '答案',
        metadata: { reasoning: '想过了' },
      },
    ])
    expect(state.reasoning).toBe('想过了')
  })

  it('collects reasoning deltas separately from the answer', () => {
    const state = run([
      { type: 'reasoning.delta', messageId: MESSAGE_ID, delta: '先想' },
      { type: 'reasoning.delta', messageId: MESSAGE_ID, delta: '再答' },
      delta('答案'),
    ])
    expect(state.reasoning).toBe('先想再答')
    expect(state.content).toBe('答案')
  })

  it('ignores events addressed to a different message', () => {
    const state = run([delta('属于别人的字', 'msg_other')])
    expect(state.content).toBe('')
  })

  it('records the run status without touching the phase', () => {
    const state = apply(run([]), { type: 'snapshot', status: 'interrupted' })
    expect(state.status).toBe('interrupted')
    expect(state.phase).toBe('connecting')
  })

  it('leaves state untouched for an unrecognised event', () => {
    const before = run([delta('稳定')])
    const after = reduce(before, { type: 'event', event: { type: 'ignored', name: 'telemetry' } })
    expect(after).toBe(before)
  })

  it('keeps the citations from context.ready', () => {
    const citation: Citation = {
      chunk_id: 'k1',
      document_id: 'd1',
      title: '笔记',
      content: '一段摘录',
      score: 0.82,
      source: 'notes.md',
    }
    const state = run([{ type: 'context.ready', citations: [citation], remainingTokens: 4096 }])
    expect(state.citations).toEqual([citation])
  })

  it('backfills audits fetched separately from the stream', () => {
    const records: RunEventRecord[] = [
      {
        id: 'e1',
        run_id: 'run_1',
        sequence: 1,
        stage: 'model_stream',
        state: 'running',
        payload: {},
        created_at: '',
      },
    ]
    const state = apply(run([]), { type: 'audits', records })
    expect(state.audits.map((row) => row.stage)).toEqual(['model_stream'])
  })
})
