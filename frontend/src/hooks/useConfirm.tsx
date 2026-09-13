/**
 * Asking before doing something that cannot be undone.
 *
 * This is `window.confirm` replaced, and the reason is not that the native one
 * is ugly. It is that the native one stops the whole page: the browser freezes
 * the tab until somebody clicks, which a streamed answer - or a run still
 * arriving over SSE - does not survive. An in-app dialog is the same question
 * asked without that cost.
 *
 * The answer arrives as a promise, so the call site reads as one line:
 *
 *     if (!(await confirm.ask({ title: '…', danger: true }))) return
 */

import { useCallback, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { ConfirmDialog, type ConfirmRequest } from '../components/ConfirmDialog'

type Answer = (answer: boolean) => void

export interface ConfirmController {
  ask(request: ConfirmRequest): Promise<boolean>
  /** Render this once, anywhere inside the component that calls `ask`. */
  dialog: ReactNode
}

export function useConfirm(): ConfirmController {
  const [request, setRequest] = useState<ConfirmRequest | null>(null)
  // The resolver lives in a ref rather than in state: it is not rendered from,
  // and putting it in state would make every open question a second render.
  const pending = useRef<Answer | null>(null)

  const ask = useCallback((next: ConfirmRequest) => {
    return new Promise<boolean>((resolve) => {
      // Opening a second question abandons the first. Nothing in the UI can do
      // that - the overlay swallows clicks - but a caller that asks twice in one
      // tick should not leave the first call waiting for an answer that never
      // comes, and `false` means "did not confirm", which is the safe reading.
      pending.current?.(false)
      pending.current = resolve
      setRequest(next)
    })
  }, [])

  const settle = useCallback((answer: boolean) => {
    const resolve = pending.current
    pending.current = null
    setRequest(null)
    resolve?.(answer)
  }, [])

  return {
    ask,
    dialog: request === null ? null : <ConfirmDialog request={request} onSettle={settle} />,
  }
}
