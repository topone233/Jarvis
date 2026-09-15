/**
 * The composer's quick prompts, fetched once per page mount.
 *
 * Failing quietly is deliberate: the buttons are an affordance, not data the
 * page needs, and a chat whose backend is down already has somewhere better
 * to say so. A page mounts fresh when navigated to, which is also why there
 * is no cache to invalidate after the settings tab saves a new list.
 */

import { useEffect, useState } from 'react'

import { getSettings } from '../api/endpoints'
import type { QuickPrompt } from '../api/types'

export function useQuickPrompts(): QuickPrompt[] {
  const [items, setItems] = useState<QuickPrompt[]>([])

  useEffect(() => {
    let alive = true
    void getSettings()
      .then((settings) => {
        if (alive) {
          setItems(settings.quick_prompts.items)
        }
      })
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [])

  return items
}
