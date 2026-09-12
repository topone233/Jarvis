/** The conversation list, and the three things the sidebar does to it. */

import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  createConversation,
  deleteConversation,
  listConversations,
  renameConversation,
} from '../api/endpoints'
import type { Conversation } from '../api/types'

export interface ConversationsController {
  conversations: Conversation[]
  reload(): Promise<void>
  create(): Promise<Conversation | null>
  remove(id: string): Promise<void>
  rename(id: string, title: string): Promise<void>
}

export function useConversations(): ConversationsController {
  const [conversations, setConversations] = useState<Conversation[]>([])

  const reload = useCallback(async () => {
    try {
      setConversations(await listConversations())
    } catch {
      // A failure here is not worth a dialog: the list stays as it was and the
      // next turn refreshes it.
    }
  }, [])

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
    () => ({ conversations, reload, create, remove, rename }),
    [conversations, reload, create, remove, rename],
  )
}
