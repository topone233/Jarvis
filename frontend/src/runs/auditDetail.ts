/**
 * What each stage's audit payload is worth saying out loud.
 *
 * The payload arrives as a bag of fields the backend happened to record. This
 * module is the one place that decides which of them are content - the lines
 * shown when a row is expanded - and which belong on the row's summary line.
 * Both are pure functions of the record, so a reloaded page draws the same
 * words the live one did, from the same two sources: the live event and the
 * trail read back from disk.
 */

import type { AuditRow } from './reducer'

const STATE_LABELS: Record<string, string> = {
  running: '',
  completed: '',
  cancelled: '已停止',
  failed: '失败',
  skipped: '已跳过',
}

const ACTION_LABELS: Record<string, string> = {
  created: '新增',
  confirmed: '确认',
  superseded: '更新',
  forgotten: '忘记',
}

const KIND_LABELS: Record<string, string> = {
  profile: '个人信息',
  preference: '偏好',
  fact: '事实',
  decision: '决定',
}

/** The lines shown when the row is expanded; empty means there is nothing to expand. */
export function detailLines(row: AuditRow): DetailLine[] {
  const payload = row.payload ?? {}
  switch (row.stage) {
    case 'context_compaction':
      return compactedLine(payload)
    case 'context_retrieval':
      return retrievalLines(payload)
    case 'model_stream':
      return modelLines(payload)
    case 'memory_write':
      return memoryLines(payload)
    case 'knowledge_tool':
      return toolLines(payload, 'knowledge')
    case 'skill_tool':
      return toolLines(payload, 'skill')
    case 'bash_tool':
      return bashLines(row, payload)
    case 'tool_call':
      return toolLines(payload, null)
    case 'tool_rounds_exhausted':
      return [{ kind: 'text', text: '连续多轮调用工具后仍未给出回答，已按轮次上限收尾。' }]
    default:
      return []
  }
}

/** One expanded line: prose, or a foldable block of raw content. */
export type DetailLine =
  { kind: 'text'; text: string } | { kind: 'code'; label: string; text: string }

/** The short verdict on the row itself, beside the label. */
export function summaryOf(row: AuditRow): string {
  const parts: string[] = []
  const stateNote = STATE_LABELS[row.state]
  if (stateNote) {
    parts.push(stateNote)
  }

  const payload = row.payload ?? {}
  if (row.stage === 'memory_write' && typeof payload.count === 'number' && payload.count > 0) {
    parts.push(`${payload.count} 条`)
  }
  if (
    (row.stage === 'knowledge_tool' || row.stage === 'skill_tool' || row.stage === 'bash_tool') &&
    typeof payload.command === 'string' &&
    payload.command !== ''
  ) {
    // Several rows can now share one stage label, so the command is what
    // tells them apart on the strip without expanding anything.
    parts.push(payload.command)
  }
  if (
    row.stage === 'context_retrieval' &&
    typeof payload.citation_count === 'number' &&
    payload.citation_count > 0
  ) {
    parts.push(`引用 ${payload.citation_count} 条`)
  }
  if (typeof payload.reason === 'string' && payload.reason !== '') {
    parts.push(payload.reason)
  }

  return parts.join(' · ')
}

function compactedLine(payload: Record<string, unknown>): DetailLine[] {
  if (payload.compacted !== true || !Array.isArray(payload.range)) {
    return []
  }
  const [from, to] = payload.range
  if (typeof from !== 'number' || typeof to !== 'number') {
    return []
  }
  return [{ kind: 'text', text: `把第 ${from} 到 ${to} 条消息折叠成了一份摘要。` }]
}

function retrievalLines(payload: Record<string, unknown>): DetailLine[] {
  // Retrieval is the step that always runs, so its report is unconditional:
  // "nothing matched" is content too, and the only alternative is a row that
  // expands into silence - the complaint this whole module exists to answer.
  const lines: DetailLine[] = []
  if (typeof payload.input_token_estimate === 'number') {
    const remaining =
      typeof payload.remaining_token_estimate === 'number'
        ? `，窗口剩 ${payload.remaining_token_estimate}`
        : ''
    lines.push({
      kind: 'text',
      text: `上下文约 ${payload.input_token_estimate} tokens${remaining}`,
    })
  }
  if (typeof payload.memory_count === 'number') {
    const keys = Array.isArray(payload.memory_keys) ? payload.memory_keys.filter(isString) : []
    lines.push({
      kind: 'text',
      text:
        keys.length > 0
          ? `注入记忆 ${payload.memory_count} 条：${keys.join('、')}`
          : `注入记忆 ${payload.memory_count} 条`,
    })
  }
  if (typeof payload.citation_count === 'number') {
    lines.push({
      kind: 'text',
      text:
        payload.citation_count > 0
          ? `召回知识 ${payload.citation_count} 条，见回答下方的引用。`
          : '召回知识 0 条',
    })
  }
  return lines
}

function modelLines(payload: Record<string, unknown>): DetailLine[] {
  const lines: DetailLine[] = []
  if (typeof payload.model === 'string' && payload.model !== '') {
    lines.push({ kind: 'text', text: `这一轮用的模型：${payload.model}` })
  }
  const usage = payload.usage
  if (isRecord(usage)) {
    const parts: string[] = []
    if (typeof usage.prompt_tokens === 'number') {
      parts.push(`输入 ${usage.prompt_tokens}`)
    }
    if (typeof usage.completion_tokens === 'number') {
      parts.push(`输出 ${usage.completion_tokens}`)
    }
    if (parts.length > 0) {
      lines.push({ kind: 'text', text: `token：${parts.join('，')}` })
    }
  }
  return lines
}

function memoryLines(payload: Record<string, unknown>): DetailLine[] {
  const lines: DetailLine[] = []
  const call = callBlock(payload)
  if (call !== null) {
    lines.push(call)
  }
  if (typeof payload.output === 'string' && payload.output !== '') {
    // What the model was told the call did - the same words it read.
    lines.push({ kind: 'code', label: '执行结果', text: payload.output })
  }
  if (Array.isArray(payload.items)) {
    for (const line of payload.items.map(actionLine)) {
      if (line !== null) {
        lines.push({ kind: 'text', text: line })
      }
    }
  }
  return lines
}

/**
 * A bash call's rows carry one thing the other tools do not: a window of
 * seconds between showing the command and running it, which is the user's
 * time to stop it. While the row is running that window is the headline -
 * first thing read, before the call JSON - and the directory it will run in
 * rides along, since the settings' working directory is not visible anywhere
 * else on the answer.
 */
function bashLines(row: AuditRow, payload: Record<string, unknown>): DetailLine[] {
  const lines: DetailLine[] = []
  if (
    row.state === 'running' &&
    typeof payload.grace_seconds === 'number' &&
    payload.grace_seconds > 0
  ) {
    lines.push({
      kind: 'text',
      text: `命令将在 ${payload.grace_seconds} 秒后执行，期间可随时停止。`,
    })
  }
  if (typeof payload.cwd === 'string' && payload.cwd !== '') {
    lines.push({ kind: 'text', text: `工作目录：${payload.cwd}` })
  }
  lines.push(...toolLines(payload, 'bash'))
  return lines
}

/**
 * One row per call since calls stopped collapsing: the AI's raw tool-call
 * object (id, name, arguments - verbatim) and the result text that went back
 * to it are the auditable whole of what happened. Payloads recorded before
 * that change carry only `command` and a character count; those still draw.
 */
function toolLines(
  payload: Record<string, unknown>,
  prefix: 'knowledge' | 'skill' | 'bash' | null,
): DetailLine[] {
  const lines: DetailLine[] = []
  const call = callBlock(payload)
  if (call !== null) {
    lines.push(call)
  }
  if (
    call === null &&
    prefix !== null &&
    typeof payload.command === 'string' &&
    payload.command !== ''
  ) {
    lines.push({
      kind: 'text',
      text:
        prefix === 'skill' && payload.trigger === 'user_request'
          ? `技能 ${payload.command}（主动触发）`
          : `${prefix} ${payload.command}`,
    })
  }
  if (typeof payload.output === 'string') {
    lines.push({ kind: 'code', label: '执行结果', text: payload.output })
  } else if (typeof payload.output_chars === 'number') {
    lines.push({ kind: 'text', text: `返回 ${payload.output_chars} 字符` })
  }
  return lines
}

/** The AI's original tool call, pretty-printed; null when the row has none. */
function callBlock(payload: Record<string, unknown>): DetailLine | null {
  if (!isRecord(payload.call)) {
    return null
  }
  return { kind: 'code', label: 'AI 原始调用', text: prettyJson(payload.call) }
}

function prettyJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function actionLine(item: unknown): string | null {
  if (!isRecord(item)) {
    return null
  }
  const action = ACTION_LABELS[String(item.action ?? '')]
  if (action === undefined || !isRecord(item.memory)) {
    return null
  }
  const memory = item.memory
  const kind = KIND_LABELS[String(memory.kind ?? '')]
  const kindPart = kind === undefined ? '' : `[${kind}] `
  let line = `${action} · ${kindPart}${String(memory.memory_key ?? '')}：${String(memory.content ?? '')}`
  if (item.action === 'forgotten' && typeof item.count === 'number' && item.count > 1) {
    line += `（连同同键的共 ${item.count} 条）`
  }
  return line
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function isString(value: unknown): value is string {
  return typeof value === 'string'
}
