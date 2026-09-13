/**
 * The way to put a note on screen.
 *
 * The context lives here and the rendering lives in `components/Toast.tsx`, the
 * same split `useConfirm` and `ConfirmDialog` use: this file exports no
 * component, so fast refresh can swap one without throwing the other away.
 *
 * The rule for what belongs in a toast is narrow, and it is in the component's
 * header - read it before adding a call site.
 */

import { createContext, useContext } from 'react'

import type { Tone } from '../toast/queue'

export interface Toaster {
  show(text: string, tone?: Tone): void
}

// `null` rather than a default object: a component that renders outside the
// provider and gets a silent no-op would look exactly like one that works.
export const ToastContext = createContext<Toaster | null>(null)

export function useToast(): Toaster {
  const toaster = useContext(ToastContext)
  if (toaster === null) {
    throw new Error('useToast was called outside ToastProvider.')
  }
  return toaster
}
