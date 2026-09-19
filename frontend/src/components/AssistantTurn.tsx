/**
 * One assistant answer, live or finished.
 *
 * Everything about *how* this renders - a partial answer that keeps growing, a
 * run that was interrupted, an answer that completed while the tab was closed -
 * comes out of the same state object, because the reducer converges all of those
 * onto the same shape. This component has no idea which one happened.
 */

import { useState } from 'react'

import type { Citation } from '../api/types'
import { useCopy } from '../hooks/useCopy'
import type { TurnState } from '../runs/reducer'
import { MarkdownWithCitations } from './Markdown'
import { ProgressStrip } from './ProgressStrip'
import { ReasoningPanel } from './ReasoningPanel'
import { CheckIcon, CopyIcon, RefreshIcon, ThumbDownIcon, ThumbUpIcon } from './icons'

export type FeedbackKind = 'up' | 'down'

export interface AssistantTurnProps {
  turn: TurnState
  /** Retries are exhausted and only a manual reconnect can help. */
  gaveUp: boolean
  onReconnect(): void
  onRegenerate(messageId: string): void
  onFeedback(messageId: string, kind: FeedbackKind): void
  /** Opens the right-side panel on a `[n]` mark or a citation chip. */
  onOpenCitation(citation: Citation): void
}

export function AssistantTurn({
  turn,
  gaveUp,
  onReconnect,
  onRegenerate,
  onFeedback,
  onOpenCitation,
}: AssistantTurnProps) {
  const [copied, copy] = useCopy()
  const [vote, setVote] = useState<FeedbackKind | null>(null)

  const busy = turn.phase === 'connecting' || turn.phase === 'streaming'
  // `interrupted` is the backend's own verdict on a run that stopped when the
  // process did. It is worth saying plainly rather than dressing it up as a
  // failure, because the answer below it is real and simply unfinished.
  const interrupted = turn.status === 'interrupted'
  const messageId = turn.assistantMessageId

  return (
    <div className="turn">
      <ProgressStrip audits={turn.audits} />
      <ReasoningPanel reasoning={turn.reasoning} thinking={busy && turn.content === ''} />

      {busy && turn.detached && (
        <div className="turn-notice">
          <span>{gaveUp ? '与后端的连接已断开。' : '与后端的连接已断开，正在重连…'}</span>
          <span className="spacer" />
          {gaveUp && <button onClick={onReconnect}>重新连接</button>}
        </div>
      )}

      {interrupted && (
        <div className="turn-notice">
          <span>这一轮在生成过程中被中断了，下面是已经生成的部分。</span>
        </div>
      )}

      {!busy && !interrupted && turn.phase === 'failed' && turn.error !== null && (
        <div className="turn-error">{turn.error}</div>
      )}

      {!busy && turn.phase === 'cancelled' && (
        <div className="turn-notice">
          <span>已停止生成。</span>
        </div>
      )}

      {turn.content !== '' && (
        <MarkdownWithCitations text={turn.content} citations={turn.citations} onOpen={onOpenCitation} />
      )}
      {busy && !turn.detached && <span className="cursor" />}

      {turn.citations.length > 0 && (
        <div className="citation-row">
          {turn.citations.map((citation) => (
            <button
              key={citation.chunk_id}
              type="button"
              className="citation-chip"
              title={citation.content}
              onClick={() => onOpenCitation(citation)}
            >
              {citation.number !== undefined && <span className="badge">{citation.number}</span>}
              <span className="label">{citation.title || citation.source || '知识库'}</span>
              <span className="score">{citation.score.toFixed(2)}</span>
            </button>
          ))}
        </div>
      )}

      {!busy && (
        <div className="turn-actions">
          <button
            type="button"
            className="icon-button"
            title="复制"
            disabled={turn.content === ''}
            onClick={() => copy(turn.content)}
          >
            {copied ? <CheckIcon size={16} /> : <CopyIcon size={16} />}
          </button>
          {messageId !== '' && (
            <button
              type="button"
              className="icon-button"
              title="重新生成"
              onClick={() => onRegenerate(messageId)}
            >
              <RefreshIcon size={16} />
            </button>
          )}
          {messageId !== '' && (
            <>
              <span className="separator" />
              <button
                type="button"
                className={`icon-button${vote === 'up' ? ' is-on' : ''}`}
                title="有帮助"
                onClick={() => {
                  setVote('up')
                  onFeedback(messageId, 'up')
                }}
              >
                <ThumbUpIcon size={16} />
              </button>
              <button
                type="button"
                className={`icon-button${vote === 'down' ? ' is-on' : ''}`}
                title="没帮助"
                onClick={() => {
                  setVote('down')
                  onFeedback(messageId, 'down')
                }}
              >
                <ThumbDownIcon size={16} />
              </button>
            </>
          )}
        </div>
      )}
    </div>
  )
}
