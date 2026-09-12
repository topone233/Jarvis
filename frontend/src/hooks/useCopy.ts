import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Copy to clipboard with a short "copied" acknowledgement.
 *
 * `navigator.clipboard` is only present in a secure context. The dev server on
 * 127.0.0.1 counts as one, but a browser pointed at a LAN address would not, so
 * the absence is handled rather than assumed away.
 */
export function useCopy(): [boolean, (text: string) => void] {
  const [copied, setCopied] = useState(false)
  const timer = useRef<number | null>(null)

  useEffect(() => {
    return () => {
      if (timer.current !== null) {
        window.clearTimeout(timer.current)
      }
    }
  }, [])

  const copy = useCallback((text: string) => {
    const clipboard = navigator.clipboard
    if (!clipboard) {
      return
    }
    void clipboard
      .writeText(text)
      .then(() => {
        setCopied(true)
        if (timer.current !== null) {
          window.clearTimeout(timer.current)
        }
        timer.current = window.setTimeout(() => setCopied(false), 1600)
      })
      .catch(() => undefined)
  }, [])

  return [copied, copy]
}
