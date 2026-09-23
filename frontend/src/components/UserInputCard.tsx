/**
 * The card that appears above the composer when a run holds for the user.
 *
 * Two shapes, one mechanism: a bash command waiting for 批准/拒绝, and the
 * model's question waiting for an option pick or free text. Whatever the
 * user answers is POSTed and the run continues; the card itself goes away
 * when the server's closing audit record lands, so what is on screen is the
 * server's verdict, not this component's optimism.
 */

import { useState } from 'react'

import type { PendingInput } from '../runs/reducer'

export interface UserInputCardProps {
  request: PendingInput
  /** POSTs the answer. Failures are the caller's to report; the card only
   *  stops offering buttons while the request is in flight. */
  onResolve(value: string): Promise<void>
}

export function UserInputCard({ request, onResolve }: UserInputCardProps) {
  const [sending, setSending] = useState(false)
  const [draft, setDraft] = useState('')

  const resolve = (value: string) => {
    if (sending || value === '') return
    setSending(true)
    // The card clears when the server's own records say the wait is over;
    // a failure just re-enables the buttons.
    void onResolve(value).catch(() => setSending(false))
  }

  return (
    <div className="user-input-card" role="alertdialog" aria-label="运行在等待你的回复">
      <div className="user-input-head">模型需要你的回复才能继续</div>
      {request.kind === 'bash' ? (
        <>
          <div className="user-input-command">
            <pre>{request.command}</pre>
          </div>
          {request.cwd !== '' && <div className="user-input-note">工作目录：{request.cwd}</div>}
          <div className="user-input-actions">
            <button
              type="button"
              className="button button-primary"
              disabled={sending}
              onClick={() => resolve('approve')}
            >
              批准执行
            </button>
            <button
              type="button"
              className="button button-ghost"
              disabled={sending}
              onClick={() => resolve('deny')}
            >
              拒绝
            </button>
          </div>
        </>
      ) : (
        <>
          <div className="user-input-question">{request.question}</div>
          {request.options.length > 0 && (
            <div className="user-input-options">
              {request.options.map((option) => (
                <button
                  key={option}
                  type="button"
                  className="user-input-option"
                  disabled={sending}
                  onClick={() => resolve(option)}
                >
                  {option}
                </button>
              ))}
            </div>
          )}
          <div className="user-input-free">
            <input
              type="text"
              value={draft}
              placeholder="或者输入你的回答…"
              disabled={sending}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault()
                  resolve(draft.trim())
                }
              }}
            />
            <button
              type="button"
              className="button button-primary"
              disabled={sending || draft.trim() === ''}
              onClick={() => resolve(draft.trim())}
            >
              发送
            </button>
          </div>
        </>
      )}
    </div>
  )
}
