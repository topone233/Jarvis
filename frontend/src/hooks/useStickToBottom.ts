import { useEffect, useRef, type RefObject } from 'react'

/**
 * Keeps the message list pinned to the newest text, without fighting the reader.
 *
 * Following stops the moment the view is scrolled away from the bottom, and
 * resumes when it comes back. Without that, reading back through a long answer
 * while it is still being written would drag the view down on every token.
 */
export function useStickToBottom(
  ref: RefObject<HTMLElement | null>,
  signal: unknown,
  resetKey: unknown,
): void {
  const stick = useRef(true)

  useEffect(() => {
    const element = ref.current
    if (element === null) {
      return
    }
    const onScroll = () => {
      const distance = element.scrollHeight - element.scrollTop - element.clientHeight
      stick.current = distance < 48
    }
    element.addEventListener('scroll', onScroll, { passive: true })
    return () => element.removeEventListener('scroll', onScroll)
  }, [ref])

  // A different conversation starts at the bottom again.
  useEffect(() => {
    stick.current = true
  }, [resetKey])

  useEffect(() => {
    const element = ref.current
    if (element !== null && stick.current) {
      element.scrollTop = element.scrollHeight
    }
  }, [ref, signal])
}
