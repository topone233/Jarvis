/**
 * The input box.
 *
 * Enter sends and Shift+Enter breaks the line, except while an input method is
 * composing - which is not a corner case in Chinese, it is how every message
 * gets typed. Without that guard, picking a candidate with Enter would send a
 * half-finished word.
 */

import { useLayoutEffect, useRef, type KeyboardEvent } from 'react'

import { TokenRing } from './TokenRing'
import { SendIcon, StopIcon } from './icons'

export interface ComposerProps {
  /** True while an answer is being produced, which turns the send button into a
   *  stop button. Left false where there is nothing to stop. */
  busy?: boolean
  remaining: number | null
  total: number | null
  onSend(text: string): void
  onStop?: () => void
}

const MAX_HEIGHT = 220

export function Composer({ busy = false, remaining, total, onSend, onStop }: ComposerProps) {
  const ref = useRef<HTMLTextAreaElement | null>(null)

  // Grow with the text up to a ceiling, then scroll inside the box.
  const fit = () => {
    const element = ref.current
    if (element === null) {
      return
    }
    element.style.height = 'auto'
    element.style.height = `${Math.min(element.scrollHeight, MAX_HEIGHT)}px`
  }

  useLayoutEffect(fit)

  function submit() {
    const element = ref.current
    if (element === null || busy) {
      return
    }
    const text = element.value.trim()
    if (text === '') {
      return
    }
    element.value = ''
    fit()
    onSend(text)
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== 'Enter' || event.shiftKey) {
      return
    }
    if (event.nativeEvent.isComposing) {
      return
    }
    event.preventDefault()
    submit()
  }

  return (
    <div className="composer">
      <div className="composer-inner">
        <div className="composer-box">
          <textarea
            ref={ref}
            rows={1}
            placeholder="给 Jarvis 发消息"
            onChange={fit}
            onKeyDown={onKeyDown}
          />
          <TokenRing remaining={remaining} total={total} />
          {busy && onStop ? (
            <button type="button" className="stop-button" title="停止生成" onClick={onStop}>
              <StopIcon size={16} />
            </button>
          ) : (
            <button type="button" className="send-button" title="发送" onClick={submit}>
              <SendIcon size={17} />
            </button>
          )}
        </div>
        <div className="composer-hint">Enter 发送，Shift + Enter 换行</div>
      </div>
    </div>
  )
}
