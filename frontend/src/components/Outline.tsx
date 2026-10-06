/**
 * The floating turn rail: one tick per user message, pinned to the right edge.
 *
 * A tick marks where a turn starts - where the user speaks. Hovering or focusing
 * one opens a preview card (the question on one line, the answer below it in
 * three), clicking scrolls to the turn, and while the page scrolls the tick the
 * viewport is in stretches to full width. The rail floats over the transcript
 * instead of taking a column of its own, and disappears entirely on narrow
 * containers - it is chrome, not content.
 *
 * The renderers only stamp `data-outline="user"` on user bubbles; this
 * component owns everything else, watching the scroll container for turns
 * appearing, changing and scrolling past. The rules live in `turnRailModel`.
 */

import { useEffect, useRef, useState, type RefObject } from 'react'

import { activeTurnIndex, collectTurns, type TurnMark } from './turnRailModel'

/** How far below the container's top edge the reading line sits. */
const READING_LINE = 96

export function TurnRail({
  containerRef,
  busy,
}: {
  containerRef: RefObject<HTMLDivElement | null>
  /** A live run is writing the last answer: its tick breathes. */
  busy: boolean
}) {
  const [turns, setTurns] = useState<TurnMark[]>([])
  const [active, setActive] = useState(-1)
  // The preview card hangs off the rail frame, positioned over the hovered
  // tick. The frame does not scroll - the inner scroller does - so an
  // absolutely placed card survives scrolling its marks.
  const [preview, setPreview] = useState<{ mark: TurnMark; top: number } | null>(null)
  const frameRef = useRef<HTMLDivElement | null>(null)
  const scrollerRef = useRef<HTMLDivElement | null>(null)
  // The gradient fades at the scroller's ends only say something while the
  // marks actually overflow; applied to a short ladder they would dim the
  // first and last tick for nothing.
  const [overflow, setOverflow] = useState<'none' | 'top' | 'bottom' | 'both'>('none')

  // Rebuild the list whenever the conversation's DOM changes. A stream writes
  // answers into the container token by token, so observation beats polling;
  // the rAF keeps a burst of mutations to one read per frame.
  useEffect(() => {
    const container = containerRef.current
    if (container === null) {
      return
    }
    const read = () => setTurns(collectTurns(container))
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

  // Which ends of the ladder are hiding scrollable marks. Re-read with the
  // turns and on resize; the rail's own height is fixed, so scroll is the
  // only other thing that can change it.
  useEffect(() => {
    const scroller = scrollerRef.current
    if (scroller === null) {
      return
    }
    const measure = () => {
      const room = scroller.scrollHeight - scroller.clientHeight
      setOverflow(
        room <= 1
          ? 'none'
          : scroller.scrollTop >= room - 1
            ? 'top'
            : scroller.scrollTop <= 1
              ? 'bottom'
              : 'both',
      )
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(scroller)
    scroller.addEventListener('scroll', measure, { passive: true })
    return () => {
      observer.disconnect()
      scroller.removeEventListener('scroll', measure)
    }
  }, [turns])

  // The lit tick follows the scroll. Positions are read per frame rather than
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
        activeTurnIndex(
          turns.map((mark) => mark.element.getBoundingClientRect().top - top),
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
  }, [turns, containerRef])

  // The lit tick must stay inside the rail's own viewport once the ladder
  // outgrows it: stream a long conversation and the newest tick keeps
  // appearing past the floor. The inner scroller nudges minimally to catch
  // it - and only itself; the transcript is never scrolled for the rail.
  useEffect(() => {
    const scroller = scrollerRef.current
    if (scroller === null || active < 0) {
      return
    }
    const mark = scroller.querySelectorAll<HTMLElement>('.turn-mark')[active]
    if (mark === undefined) {
      return
    }
    const head = scroller.getBoundingClientRect().top
    const top = mark.getBoundingClientRect().top
    const bottom = top + mark.clientHeight
    if (top < head) {
      scroller.scrollTop += top - head
    } else if (bottom > head + scroller.clientHeight) {
      scroller.scrollTop += bottom - head - scroller.clientHeight
    }
  }, [active, turns])

  function jump(mark: TurnMark) {
    const container = containerRef.current
    if (container === null) {
      return
    }
    const target =
      mark.element.getBoundingClientRect().top -
      container.getBoundingClientRect().top +
      container.scrollTop -
      12
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    container.scrollTo({ top: target, behavior: reduced ? 'auto' : 'smooth' })
  }

  // The card's top follows the tick, in the rail's own coordinates: the card
  // hangs off the rail box, so keeping it inside the band means keeping it
  // inside the box - a first or last tick can never push it past the rail's
  // floor, which is itself pinned clear of the composer.
  function showPreview(mark: TurnMark, target: HTMLElement) {
    const rail = frameRef.current
    if (rail === null) {
      return
    }
    const height = 92
    const markTop = target.getBoundingClientRect().top - rail.getBoundingClientRect().top
    const top = Math.max(0, Math.min(markTop - height / 2, Math.max(0, rail.clientHeight - height)))
    setPreview({ mark, top })
  }

  // One turn is nothing to navigate; two is the smallest thing a rail helps with.
  if (turns.length < 2) {
    return null
  }

  return (
    <div className="turn-rail-slot">
      <div className="turn-rail" ref={frameRef}>
        <div
          className={`turn-rail-scroller${overflow === 'top' || overflow === 'both' ? ' fade-top' : ''}${
            overflow === 'bottom' || overflow === 'both' ? ' fade-bottom' : ''
          }`}
          ref={scrollerRef}
        >
          <div className="turn-rail-marks">
            {turns.map((mark, index) => {
              const isBusy = busy && index === turns.length - 1
              return (
                <button
                  key={index}
                  type="button"
                  className={`turn-mark${index === active ? ' is-active' : ''}${
                    isBusy ? ' is-busy' : ''
                  }`}
                  aria-label={`跳到第 ${index + 1} 回合`}
                  onClick={() => jump(mark)}
                  onMouseEnter={(event) => showPreview(mark, event.currentTarget)}
                  onMouseLeave={() => setPreview(null)}
                  onFocus={(event) => showPreview(mark, event.currentTarget)}
                  onBlur={() => setPreview(null)}
                >
                  <span className="turn-tick" />
                </button>
              )
            })}
          </div>
        </div>
        {preview !== null && (
          <div className="turn-preview" style={{ top: preview.top }}>
            {preview.mark.prompt !== '' && <div className="turn-preview-q">{preview.mark.prompt}</div>}
            {preview.mark.response !== '' && (
              <div className="turn-preview-a">{preview.mark.response}</div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
