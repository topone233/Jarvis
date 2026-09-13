/**
 * The dialog itself. `useConfirm` in `hooks/useConfirm.tsx` is what opens it;
 * this file only knows how to look like a question and report the answer.
 *
 * Built on `<dialog>` + `showModal()` rather than a positioned div, because the
 * platform already does the parts that are easy to get wrong: the top layer, the
 * backdrop, Escape, and a real focus trap that keeps Tab from wandering into the
 * page behind.
 */

import { useEffect, useRef } from 'react'

import { CloseIcon } from './icons'

export interface ConfirmRequest {
  title: string
  /** Optional second line, for saying what exactly is about to happen. */
  body?: string
  /** Defaults to 确定. Name the action instead when it is a specific one. */
  confirmLabel?: string
  /** For actions that destroy something: outlined in the palette's red. */
  danger?: boolean
}

export function ConfirmDialog({
  request,
  onSettle,
}: {
  request: ConfirmRequest
  onSettle(answer: boolean): void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const confirmButton = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    const element = dialog.current
    if (element !== null && !element.open) {
      element.showModal()
    }
    // Focus lands on the button that carries out the action, so the keyboard
    // path is the same as the one the mouse takes: Tab moves to the other
    // button, Enter answers.
    confirmButton.current?.focus()
  }, [])

  return (
    <dialog
      ref={dialog}
      className="confirm-dialog"
      // Escape closes a modal dialog by itself, and closing it that way leaves
      // the promise unresolved - the state would still say a question is open
      // while nothing is on screen. So the default is taken over here.
      onCancel={(event) => {
        event.preventDefault()
        onSettle(false)
      }}
      // A click on the backdrop is delivered to the dialog element itself. The
      // box inside it fills the element, so this only fires from outside the box.
      onClick={(event) => {
        if (event.target === dialog.current) {
          onSettle(false)
        }
      }}
    >
      <div className="confirm-box">
        {/* Withdrawing, so it settles the same way 取消 does. Not the button that
            gets focus: the keyboard still lands on the action. */}
        <button
          type="button"
          className="icon-button confirm-close"
          aria-label="关闭"
          title="关闭"
          onClick={() => onSettle(false)}
        >
          <CloseIcon size={16} />
        </button>
        <h2 className="confirm-title">{request.title}</h2>
        {request.body !== undefined && <p className="confirm-body">{request.body}</p>}
        <div className="confirm-actions">
          <button type="button" className="button button-ghost" onClick={() => onSettle(false)}>
            取消
          </button>
          <button
            ref={confirmButton}
            type="button"
            className={`button ${request.danger ? 'button-danger' : 'button-primary'}`}
            onClick={() => onSettle(true)}
          >
            {request.confirmLabel ?? '确定'}
          </button>
        </div>
      </div>
    </dialog>
  )
}
