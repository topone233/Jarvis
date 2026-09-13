/**
 * Short notes about things that left nothing behind on screen.
 *
 * The rule for what belongs here is narrow: an action whose result is gone from
 * the screen by the time it is done. Deleting a row makes the row disappear -
 * that is the result, and it says nothing about where the row went. An action
 * whose outcome is still visible afterwards gets nothing: sending a message
 * shows the message, saving a setting shows 「已保存。」 next to the setting, and
 * a toast for those would be a second voice saying the same thing.
 *
 * Nothing else changes: failures stay where the thing that failed is, so the
 * message is still on screen while the user does something about it.
 *
 * `useToast` is in `hooks/useToast.ts` - this file exports components only.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import { ToastContext } from '../hooks/useToast'
import { drop, push, type ToastItem, type Tone } from '../toast/queue'

/** Long enough to read a short sentence, short enough not to sit in the way. */
const OK_MS = 2400
/** Longer, because a failure is worth reading twice. */
const BAD_MS = 4000

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([])

  const show = useCallback((text: string, tone: Tone = 'ok') => {
    setToasts((current) => push(current, text, tone))
  }, [])

  const dismiss = useCallback((id: number) => {
    setToasts((current) => drop(current, id))
  }, [])

  // Stable, so that a component calling `show` from inside an effect does not
  // re-run it on every render of the provider.
  const toaster = useMemo(() => ({ show }), [show])

  return (
    <ToastContext.Provider value={toaster}>
      {children}
      {toasts.length > 0 && (
        // Polite: a toast never interrupts, and nothing here needs an answer.
        <div className="toast-stack" role="status" aria-live="polite">
          {toasts.map((item) => (
            <Toast key={item.id} item={item} onDismiss={dismiss} />
          ))}
        </div>
      )}
    </ToastContext.Provider>
  )
}

function Toast({ item, onDismiss }: { item: ToastItem; onDismiss(id: number): void }) {
  // The timer belongs to the toast, and its cleanup cancels it whichever way the
  // toast leaves - timed out, clicked away, or pushed out by a fourth one. A
  // timer that outlived its toast would come back later and dismiss whatever
  // happened to be showing.
  useEffect(() => {
    const timer = window.setTimeout(() => onDismiss(item.id), item.tone === 'bad' ? BAD_MS : OK_MS)
    return () => window.clearTimeout(timer)
  }, [item.id, item.tone, onDismiss])

  return (
    // A button, so clicking it away is reachable from the keyboard too.
    <button type="button" className={`toast is-${item.tone}`} onClick={() => onDismiss(item.id)}>
      <span className="toast-dot" aria-hidden="true" />
      <span className="toast-text">{item.text}</span>
    </button>
  )
}
