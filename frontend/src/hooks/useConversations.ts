/** The conversation list, and the four things the sidebar does to it. */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  createConversation,
  deleteConversation,
  listConversations,
  renameConversation,
} from '../api/endpoints'
import type { Conversation } from '../api/types'

/** Keystrokes settle this long before a fetch is sent, so typing stays fluid. */
const SEARCH_DEBOUNCE_MS = 250

export interface ConversationsController {
  conversations: Conversation[]
  reload(): Promise<void>
  create(): Promise<Conversation | null>
  remove(id: string): Promise<void>
  rename(id: string, title: string): Promise<void>
  /** The keyword the current list was filtered by, as typed. */
  search: string
  setSearch(search: string): void
}

export function useConversations(): ConversationsController {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [search, setSearch] = useState('')
  // The keyword that actually reaches the API trails the typing by a beat.
  const [settledSearch, setSettledSearch] = useState('')
  // Only the newest request may land: two searches can overlap when the
  // debounce fires twice in a row, and the slower stale one must not win.
  const reloadSequence = useRef(0)

  useEffect(() => {
    const timer = setTimeout(() => setSettledSearch(search), SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [search])

  const reload = useCallback(async () => {
    const seq = ++reloadSequence.current
    try {
      const result = await listConversations(settledSearch ? { query: settledSearch } : {})
      if (seq === reloadSequence.current) {
        setConversations(result)
      }
    } catch {
      // A failure here is not worth a dialog: the list stays as it was and the
      // next turn refreshes it.
    }
  }, [settledSearch])

  useEffect(() => {
    void reload()
  }, [reload])

  const create = useCallback(async () => {
    try {
      const conversation = await createConversation()
      setConversations((current) => [conversation, ...current])
      return conversation
    } catch {
      return null
    }
  }, [])

  const remove = useCallback(async (id: string) => {
    await deleteConversation(id)
    setConversations((current) => current.filter((item) => item.id !== id))
  }, [])

  const rename = useCallback(async (id: string, title: string) => {
    const updated = await renameConversation(id, title)
    setConversations((current) => current.map((item) => (item.id === id ? updated : item)))
  }, [])

  // Stable identity, so callers can depend on the controller without the object
  // literal changing under them on every render.
  return useMemo(
    () => ({ conversations, reload, create, remove, rename, search, setSearch }),
    [conversations, reload, create, remove, rename, search],
  )
}
