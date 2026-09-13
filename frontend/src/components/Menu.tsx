/**
 * A small dropdown: a button, and a panel that opens above it.
 *
 * The panel opens upwards because its one caller sits at the bottom of the
 * window, where a menu opening downwards would be off screen.
 *
 * The panel holds a list of items and, under them, a row of the caller's own -
 * something to set rather than something to pick. That is why choosing an item
 * does **not** close the panel: a choice is not the end of the visit when there
 * is a second thing in there to adjust, and closing on the first pick would
 * make setting both take two openings.
 *
 * The remaining ways out are the ones a person tries: press Escape, or click
 * anywhere else. Escape also puts the focus back on the button, which is where
 * it was before the panel opened and where a keyboard user expects to still be.
 */

import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { CheckIcon, ChevronDownIcon } from './icons'

export interface MenuItem {
  id: string
  label: string
}

export function Menu({
  label,
  title,
  items,
  selected,
  onPick,
  footer,
}: {
  /** What the closed button says. One of the items, normally. */
  label: string
  /** What the button is for, for the tooltip. */
  title: string
  items: MenuItem[]
  /** The item in use. Marked rather than hidden: it is the list of what is on
   *  offer, and a menu that omits the current value cannot say what it is. */
  selected: string
  onPick(id: string): void
  /** Sets something rather than choosing something. Pinned below the list, so
   *  a long list of items does not scroll it away. */
  footer?: ReactNode
}) {
  const [open, setOpen] = useState(false)
  const boxRef = useRef<HTMLDivElement | null>(null)
  const buttonRef = useRef<HTMLButtonElement | null>(null)

  useEffect(() => {
    if (!open) {
      return
    }
    const onPointerDown = (event: PointerEvent) => {
      if (boxRef.current !== null && !boxRef.current.contains(event.target as Node)) {
        setOpen(false)
      }
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') {
        return
      }
      setOpen(false)
      buttonRef.current?.focus()
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  return (
    <div className="menu" ref={boxRef}>
      <button
        ref={buttonRef}
        type="button"
        className="menu-trigger"
        title={title}
        aria-expanded={open}
        onClick={() => setOpen((shown) => !shown)}
      >
        <span className="menu-label">{label}</span>
        <ChevronDownIcon size={14} />
      </button>
      {open && (
        <div className="menu-panel">
          <ul className="menu-list">
            {items.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  className={`menu-item${item.id === selected ? ' is-selected' : ''}`}
                  aria-current={item.id === selected}
                  title={item.label}
                  onClick={() => onPick(item.id)}
                >
                  <span className="menu-item-label">{item.label}</span>
                  {item.id === selected && <CheckIcon size={14} />}
                </button>
              </li>
            ))}
          </ul>
          {footer !== undefined && <div className="menu-foot">{footer}</div>}
        </div>
      )}
    </div>
  )
}
