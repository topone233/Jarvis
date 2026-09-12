/**
 * The model's thinking, when it produces any.
 *
 * Open while the answer has not started, because at that moment the thinking is
 * the only thing happening. Once text starts arriving it folds away on its own -
 * unless the reader has opened or closed it themselves, in which case their
 * choice wins for the rest of the turn.
 */

import { useState } from 'react'

import { ChevronRightIcon } from './icons'

export function ReasoningPanel({ reasoning, thinking }: { reasoning: string; thinking: boolean }) {
  // `null` means "follow the automatic behaviour"; a boolean means the reader
  // overrode it.
  const [override, setOverride] = useState<boolean | null>(null)

  if (reasoning === '') {
    return null
  }
  const open = override ?? thinking

  return (
    <div className="reasoning">
      <button
        type="button"
        className={`reasoning-head${open ? ' is-open' : ''}`}
        onClick={() => setOverride(!open)}
        aria-expanded={open}
      >
        <ChevronRightIcon size={14} className="chevron" />
        <span>{thinking ? '思考中…' : '思考过程'}</span>
      </button>
      {open && <div className="reasoning-body">{reasoning}</div>}
    </div>
  )
}
