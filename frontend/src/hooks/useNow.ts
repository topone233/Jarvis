/**
 * The current time, re-read on a timer while something is still moving.
 *
 * A duration that is still growing has to be redrawn to look like it is moving,
 * and the strip is the only thing that wants to know - so the clock lives there
 * rather than in the page above it. When nothing is running it stops, because a
 * finished answer that keeps re-rendering twenty times a second for the rest of
 * the session is a waste nobody sees.
 */

import { useEffect, useState } from 'react'

/** 50ms is a twentieth of a second: enough for the hundredths to move smoothly. */
const DEFAULT_INTERVAL_MS = 50

export function useNow(active: boolean, intervalMs: number = DEFAULT_INTERVAL_MS): number {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (!active) {
      return
    }
    // Read once on the way in, so resuming a timer never shows the moment the
    // last one was stopped.
    setNow(Date.now())
    const timer = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(timer)
  }, [active, intervalMs])

  return now
}
