/**
 * The input box.
 *
 * Enter sends and Shift+Enter breaks the line, except while an input method is
 * composing - which is not a corner case in Chinese, it is how every message
 * gets typed. Without that guard, picking a candidate with Enter would send a
 * half-finished word.
 */

import { useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'

import { SendIcon, StopIcon } from './icons'

export interface ComposerProps {
  /** True while an answer is being produced, which turns the send button into a
   *  stop button. Left false where there is nothing to stop. */
  busy?: boolean
  /** The controls the page wants beside the send button - the model, the
   *  thinking dial. The composer decides where they go, not what they are. */
  controls?: ReactNode
  onSend(text: string): void
  onStop?: () => void
}

const MAX_HEIGHT = 220

export function Composer({ busy = false, controls, onSend, onStop }: ComposerProps) {
  const ref = useRef<HTMLTextAreaElement | null>(null)
  // Whether there is anything worth sending. The textarea is uncontrolled - the
  // height is set from its own scrollHeight and never from React state - so this
  // is the one thing about its contents the render needs to see. Whitespace
  // does not count, which is the same rule `submit` refuses on.
  const [filled, setFilled] = useState(false)

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

  function changed() {
    fit()
    const element = ref.current
    setFilled(element !== null && element.value.trim() !== '')
  }

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
    setFilled(false)
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
            onChange={changed}
            onKeyDown={onKeyDown}
          />
          {controls}
          {busy && onStop ? (
            <button type="button" className="stop-button" title="停止生成" onClick={onStop}>
              <StopIcon size={16} />
            </button>
          ) : (
            <button
              type="button"
              className={filled ? 'send-button is-filled' : 'send-button'}
              title="发送"
              onClick={submit}
            >
              <SendIcon size={17} />
            </button>
          )}
        </div>
        <div className="composer-hint">Enter 发送，Shift + Enter 换行</div>
      </div>
    </div>
  )
}
