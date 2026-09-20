/**
 * The right-hand rail: one short line per section of the conversation.
 *
 * A user message opens a section; a heading inside an answer marks a spot
 * within it. Hovering a line says what is there, clicking scrolls to it, and
 * while the page scrolls the line the viewport is in lights up. The rail is
 * chrome, not content - bare lines, no box - and it stays out of the way
 * entirely until there is something worth navigating.
 *
 * The renderers only stamp `data-outline` attributes; this component owns
 * everything else, watching the scroll container for those stamps appearing,
 * changing and scrolling past. The rules themselves live in `outlineModel`.
 */

import { useEffect, useState, type RefObject } from 'react'

import { activeIndex, collectOutline, type OutlineEntry } from './outlineModel'

/** How far below the container's top edge the reading line sits. */
const READING_LINE = 96

export function Outline({ containerRef }: { containerRef: RefObject<HTMLDivElement | null> }) {
  const [entries, setEntries] = useState<OutlineEntry[]>([])
  const [active, setActive] = useState(-1)
  // One tooltip for the whole rail, positioned from the hovered line. It is
  // fixed to the viewport rather than absolutely placed because the rail
  // scrolls, and a scroll container clips anything that pokes out of it - the
  // tooltip's whole job is to poke out to the left.
  const [tip, setTip] = useState<{ label: string; x: number; y: number } | null>(null)

  // Rebuild the list whenever the conversation's DOM changes. A stream writes
  // headings into the container token by token, so observation beats polling;
  // the rAF keeps a burst of mutations to one read per frame.
  useEffect(() => {
    const container = containerRef.current
    if (container === null) {
      return
    }
    const read = () => setEntries(collectOutline(container))
    read()
    let queued = false
    const observer = new MutationObserver(() => {
      if (queued) {
        return
      }
      queued = true
      requestAnimationFrame(() => {
        queued = false
        read()
      })
    })
    observer.observe(container, { childList: true, subtree: true })
    return () => observer.disconnect()
  }, [containerRef])

  // The lit line follows the scroll. Positions are read per frame rather than
  // cached: the container resizes, and answers above the fold keep growing.
  useEffect(() => {
    const container = containerRef.current
    if (container === null) {
      return
    }
    let frame: number | null = null
    const measure = () => {
      frame = null
      const top = container.getBoundingClientRect().top
      setActive(
        activeIndex(
          entries.map((entry) => entry.element.getBoundingClientRect().top - top),
          READING_LINE,
        ),
      )
    }
    const onScroll = () => {
      if (frame === null) {
        frame = requestAnimationFrame(measure)
      }
    }
    measure()
    container.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      container.removeEventListener('scroll', onScroll)
      if (frame !== null) {
        cancelAnimationFrame(frame)
      }
    }
  }, [entries, containerRef])

  function jump(entry: OutlineEntry) {
    const container = containerRef.current
    if (container === null) {
      return
    }
    const target =
      entry.element.getBoundingClientRect().top -
      container.getBoundingClientRect().top +
      container.scrollTop -
      12
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    container.scrollTo({ top: target, behavior: reduced ? 'auto' : 'smooth' })
  }

  function showTip(target: HTMLElement, entry: OutlineEntry) {
    const rect = target.getBoundingClientRect()
    setTip({ label: entry.label, x: rect.left, y: rect.top + rect.height / 2 })
  }

  // One section is nothing to navigate; two is the smallest thing a rail helps with.
  if (entries.length < 2) {
    return null
  }

  // The tip is a sibling of the nav, not a child: as a child it would become
  // the nav's `:last-child`, which the centreing auto-margins key on, and the
  // whole rail would jump the moment a tooltip appeared - unhovering it again,
  // and flickering for as long as the cursor sits on a line.
  return (
    <>
      <nav className="outline" aria-label="对话目录">
        {entries.map((entry, index) => (
          <button
            key={index}
            type="button"
            className={`outline-item is-l${entry.level}${index === active ? ' is-active' : ''}`}
            onClick={() => jump(entry)}
            onMouseEnter={(event) => showTip(event.currentTarget, entry)}
            onMouseLeave={() => setTip(null)}
            onFocus={(event) => showTip(event.currentTarget, entry)}
            onBlur={() => setTip(null)}
          >
            <span className="outline-line" />
          </button>
        ))}
      </nav>
      {tip !== null && (
        <span className="outline-tip" style={{ top: tip.y, left: tip.x }}>
          {tip.label}
        </span>
      )}
    </>
  )
}
