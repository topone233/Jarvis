/**
 * One conversation.
 *
 * The two things this page has to get right:
 *
 * 1. Loading it and *watching* it are the same act. If the newest answer belongs
 *    to a run that is still going, loading the page attaches to that run; if the
 *    run already ended - normally, or because the process was killed - the same
 *    load finds that out and labels the answer accordingly.
 * 2. Nothing about a run is inferred. The status comes from `GET /api/runs/{id}`
 *    and the text comes from the stream, so a reloaded tab and a tab that never
 *    left show the same thing.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router'

import { getRun, listMessages, listModelProfiles, sendFeedback } from '../api/endpoints'
import type { Message, ModelProfile } from '../api/types'
import { Composer } from '../components/Composer'
import { MessageList, type LastRun } from '../components/MessageList'
import type { FeedbackKind } from '../components/AssistantTurn'
import type { ConversationsController } from '../hooks/useConversations'
import { useStickToBottom } from '../hooks/useStickToBottom'
import { useRun } from '../runs/useRun'

export function ChatPage({
  conversationId,
  conversations,
}: {
  conversationId: string
  conversations: ConversationsController
}) {
  const location = useLocation()
  const navigate = useNavigate()

  const [messages, setMessages] = useState<Message[]>([])
  const [profiles, setProfiles] = useState<ModelProfile[]>([])
  const [pendingUser, setPendingUser] = useState<string | null>(null)
  const [lastRun, setLastRun] = useState<LastRun | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)

  const reload = conversations.reload
  const refresh = useCallback(async () => {
    try {
      const [loaded] = await Promise.all([listMessages(conversationId), reload()])
      // Both updates land in the same commit, so the optimistic bubble is never
      // on screen at the same time as the stored one it duplicates.
      setMessages(loaded)
      setPendingUser(null)
    } catch {
      // The run's own events still drive the screen; the list catches up on the
      // next turn.
    }
  }, [conversationId, reload])

  const run = useRun(conversationId, () => void refresh())
  const { attach, cancel, regenerate, reconnect, send, turn, gaveUp } = run

  // Load the conversation, and reattach to it if it is still being written.
  useEffect(() => {
    let dropped = false
    void (async () => {
      const [loaded, profileList] = await Promise.all([
        listMessages(conversationId),
        listModelProfiles().catch(() => [] as ModelProfile[]),
      ])
      if (dropped) {
        return
      }
      setMessages(loaded)
      setProfiles(profileList)

      const open = newestRun(loaded)
      if (open === null) {
        return
      }
      const record = await getRun(open.runId)
      if (dropped) {
        return
      }
      setLastRun({ runId: open.runId, status: record.status })
      if (record.status === 'running' || record.status === 'cancelling') {
        attach(open.runId, {
          assistantMessageId: open.messageId,
          content: open.content,
          reasoning: open.reasoning,
        })
      }
    })()
    return () => {
      dropped = true
    }
  }, [conversationId, attach])

  // A run that has just ended is only reflected in the list once it is re-read.
  const phase = turn?.phase ?? null
  const localId = turn?.localId ?? null
  useEffect(() => {
    if (phase === null || phase === 'connecting' || phase === 'streaming') {
      return
    }
    void refresh()
  }, [phase, localId, refresh])

  const send_ = useCallback(
    (text: string) => {
      setPendingUser(text)
      send(text)
    },
    [send],
  )

  // A message handed over by the new-chat screen. Consumed once, and the history
  // entry is cleared so a reload cannot send it a second time.
  const seeded = useRef(false)
  useEffect(() => {
    if (seeded.current) {
      return
    }
    seeded.current = true
    const text = (location.state as { firstMessage?: string } | null)?.firstMessage
    if (typeof text !== 'string' || text === '') {
      return
    }
    navigate(location.pathname, { replace: true, state: null })
    send_(text)
  }, [location.state, location.pathname, navigate, send_])

  const onFeedback = useCallback((messageId: string, kind: FeedbackKind) => {
    void sendFeedback(messageId, kind).catch(() => undefined)
  }, [])

  const busy = phase === 'connecting' || phase === 'streaming'
  const conversation = conversations.conversations.find((item) => item.id === conversationId)
  const total = contextBudget(profiles, conversation?.model_profile_id ?? null)

  useStickToBottom(scrollRef, `${turn?.content.length ?? 0}:${messages.length}`, conversationId)

  return (
    <div className="chat">
      <header className="chat-header">
        <span className="chat-title">{conversation?.title ?? '对话'}</span>
      </header>

      <div className="messages" ref={scrollRef}>
        <div className="messages-inner">
          <MessageList
            messages={messages}
            turn={turn}
            lastRun={lastRun}
            gaveUp={gaveUp}
            pendingUser={pendingUser}
            onReconnect={reconnect}
            onRegenerate={regenerate}
            onFeedback={onFeedback}
          />
        </div>
      </div>

      <Composer
        busy={busy}
        remaining={turn?.remainingTokens ?? null}
        total={total}
        onSend={send_}
        onStop={cancel}
      />
    </div>
  )
}

interface OpenRun {
  runId: string
  messageId: string
  content: string
  reasoning: string
}

/**
 * The run behind the newest answer, if there is one to ask about.
 *
 * Only the last message matters: `regenerate` refuses to replace anything but
 * the newest answer, so an older run can never still be producing.
 */
function newestRun(messages: Message[]): OpenRun | null {
  const last = messages[messages.length - 1]
  if (last === undefined || last.role !== 'assistant') {
    return null
  }
  const runId = last.metadata?.run_id
  if (typeof runId !== 'string' || runId === '') {
    return null
  }
  return {
    runId,
    messageId: last.id,
    content: last.content,
    reasoning: last.metadata?.reasoning ?? '',
  }
}

/** How many tokens the conversation may spend on input before compaction. */
function contextBudget(profiles: ModelProfile[], profileId: string | null): number | null {
  const profile =
    profiles.find((item) => item.id === profileId) ?? profiles.find((item) => item.is_default === 1)
  if (profile === undefined) {
    return null
  }
  return Math.max(profile.context_window - profile.output_token_reserve, 0)
}
