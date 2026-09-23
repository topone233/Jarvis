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

import { getRun, listConversationRunEvents, listMessages, sendFeedback } from '../api/endpoints'
import type { ThinkingLevel } from '../api/thinking'
import type { Citation, Message, RunEventRecord } from '../api/types'
import { Composer } from '../components/Composer'
import { CitationPanel } from '../components/CitationPanel'
import { MessageList, type LastRun } from '../components/MessageList'
import { ModelControls } from '../components/ModelControls'
import { Outline } from '../components/Outline'
import { UserInputCard } from '../components/UserInputCard'
import type { FeedbackKind } from '../components/AssistantTurn'
import type { ConversationsController } from '../hooks/useConversations'
import { useModelChoice } from '../hooks/useModelChoice'
import { useQuickPrompts } from '../hooks/useQuickPrompts'
import { useStickToBottom } from '../hooks/useStickToBottom'
import { useToast } from '../hooks/useToast'
import { takePendingImages } from '../pendingImages'
import { useRun } from '../runs/useRun'

/**
 * What the new-chat screen hands over: the first message, and the model and
 * thinking dial that was on screen when it was sent.
 *
 * The choice has to travel with the message, because the conversation it is
 * about is created on the way: a message sent with qwen selected would
 * otherwise be answered by whatever the default profile names.
 */
interface SeededTurn {
  firstMessage?: string
  chatModel?: string
  level?: ThinkingLevel
}

export function ChatPage({
  conversationId,
  conversations,
}: {
  conversationId: string
  conversations: ConversationsController
}) {
  const location = useLocation()
  const navigate = useNavigate()
  const seeded = location.state as SeededTurn | null
  const quickPrompts = useQuickPrompts()

  const [messages, setMessages] = useState<Message[]>([])
  const [trail, setTrail] = useState<Record<string, RunEventRecord[]>>({})
  const [pendingUser, setPendingUser] = useState<{ text: string; images: string[] } | null>(null)
  const [lastRun, setLastRun] = useState<LastRun | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  // The citation whose excerpt or document the right drawer shows. Null is
  // the drawer closed - the conversation owns the full width again.
  const [activeCitation, setActiveCitation] = useState<Citation | null>(null)
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

  // The steps behind every answer in this conversation, keyed by run id. The
  // stream is no help here: it only ever speaks for the run it is attached to,
  // so every older answer would have to go without.
  const readTrail = useCallback(
    () =>
      listConversationRunEvents(conversationId).catch(
        () => ({}) as Record<string, RunEventRecord[]>,
      ),
    [conversationId],
  )

  const run = useRun(conversationId, () => void refresh())
  const { attach, cancel, regenerate, reconnect, resolveInput, send, turn, gaveUp } = run
  const toast = useToast()

  // A refused answer means the wait ended without us: another window answered
  // first, or the run finished. The card clears with the server's own records;
  // the toast is just the reason the button seemed not to work.
  const onResolveInput = useCallback(
    (value: string) =>
      resolveInput(value).catch(() => toast.show('这个请求已经结束了，不需要再回复。', 'bad')),
    [resolveInput, toast],
  )

  const conversation = conversations.conversations.find((item) => item.id === conversationId)
  // What the composer is showing, and what the next turn will carry. The seed is
  // only read on the first render, before the effect below clears the history
  // entry it came in on.
  const choice = useModelChoice(conversation?.model_profile_id ?? null, seeded ?? undefined)
  const { model, level } = choice

  // Whether this page is already watching a turn, read by the load below without
  // making it re-run on every token. Kept in an effect rather than assigned
  // during render, and declared before that load so it is up to date by the time
  // the load runs.
  const watchingRef = useRef(false)
  useEffect(() => {
    watchingRef.current = turn !== null
  }, [turn])

  // Load the conversation, and reattach to it if it is still being written.
  useEffect(() => {
    let dropped = false
    void (async () => {
      // Its failure gets its own handler because it is the one that means "there
      // is no such conversation here". Letting it reject the way it used to is
      // what turned a missing conversation into a page that rendered nothing and
      // said nothing - indistinguishable from an empty conversation.
      let loaded: Message[]
      try {
        loaded = await listMessages(conversationId)
      } catch (cause) {
        if (!dropped) {
          setLoadError(describeLoadFailure(cause))
        }
        return
      }
      if (dropped) {
        return
      }
      setLoadError(null)
      setMessages(loaded)
      // Development runs this load twice, and the second pass can land after the
      // send was stored - in which case the history it just read already holds
      // the message the optimistic bubble stands for, and the same words are on
      // screen twice until the run ends. Matching the text is safe *here* because
      // this effect runs at mount and when the conversation changes, never while
      // a conversation is in use: a message that matches the one just sent can
      // only be that one.
      setPendingUser((pending) => (alreadyStored(loaded, pending) ? null : pending))

      // Not worth failing the load over: a missing trail costs the steps drawn
      // under answers that are already on screen anyway. The profile list is the
      // composer's own business, fetched by the hook that needs it.
      const runEvents = await readTrail()
      if (dropped) {
        return
      }
      setTrail(runEvents)

      const open = newestRun(loaded)
      if (open === null) {
        return
      }
      // A missing run record costs nothing worth failing the load over: the
      // answer is on screen either way, and all that is lost is the label saying
      // how its run ended plus the chance to reattach to a live one.
      const record = await getRun(open.runId).catch(() => null)
      if (dropped || record === null) {
        return
      }
      setLastRun({ runId: open.runId, status: record.status })
      if (record.status === 'running' || record.status === 'cancelling') {
        // A run found on disk is only ours to watch if nobody here is already
        // watching one. Otherwise a send and a re-read of the history can both
        // answer for the same run: the second takes over the connection, which
        // drops the POST stream that was already delivering the answer and puts
        // the user's own message back on screen next to the optimistic bubble
        // still showing it.
        if (watchingRef.current) {
          return
        }
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
  }, [conversationId, attach, readTrail])

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
    (text: string, images: string[] = []) => {
      setPendingUser({ text, images })
      send(text, { chatModel: model, level }, images)
      // The answer this one replaces has just finished, which is the moment its
      // trail on disk becomes complete - the memory write is reported after the
      // answer is already on screen, so a read taken while it was running came
      // back short. Without this the previous answer would lose its last step
      // the instant it stopped being the live one.
      void readTrail().then(setTrail)
    },
    [readTrail, send, model, level],
  )

  const onRegenerate = useCallback(
    (messageId: string) => {
      // Same reason as a send: the answer being replaced is over.
      void readTrail().then(setTrail)
      regenerate(messageId, { chatModel: model, level })
    },
    [readTrail, regenerate, model, level],
  )

  // A message handed over by the new-chat screen. Consumed once, and the history
  // entry is cleared so a reload cannot send it a second time. The images ride
  // the in-memory handoff rather than the history entry - too big for it - and
  // are taken even when the text turns out to be absent, because an
  // image-only first message is a real send.
  const consumed = useRef(false)
  useEffect(() => {
    if (consumed.current) {
      return
    }
    consumed.current = true
    const seededText = seeded?.firstMessage
    const text = typeof seededText === 'string' ? seededText : ''
    const images = takePendingImages()
    if (text === '' && images.length === 0) {
      return
    }
    navigate(location.pathname, { replace: true, state: null })
    send_(text, images)
  }, [seeded, location.pathname, navigate, send_])

  const onFeedback = useCallback((messageId: string, kind: FeedbackKind) => {
    void sendFeedback(messageId, kind).catch(() => undefined)
  }, [])

  const busy = phase === 'connecting' || phase === 'streaming'

  useStickToBottom(scrollRef, `${turn?.content.length ?? 0}:${messages.length}`, conversationId)

  return (
    <div className="chat">
      <div className="chat-body">
        <div className="messages" ref={scrollRef}>
          <div className="messages-inner">
            {loadError !== null && (
              <div className="load-error" role="alert">
                <p className="load-error-title">{loadError}</p>
                <p className="load-error-hint">
                  左侧列出的是当前数据目录里的对话。这个对话可能在另一个数据目录里，也可能已经被删掉了。
                </p>
              </div>
            )}
            <MessageList
              messages={messages}
              turn={turn}
              lastRun={lastRun}
              trail={trail}
              gaveUp={gaveUp}
              pendingUser={pendingUser}
              onReconnect={reconnect}
              onRegenerate={onRegenerate}
              onFeedback={onFeedback}
              onOpenCitation={setActiveCitation}
            />
          </div>
        </div>

        <Outline containerRef={scrollRef} />
        {activeCitation !== null && (
          <CitationPanel citation={activeCitation} onClose={() => setActiveCitation(null)} />
        )}
      </div>

      {turn?.pendingInput != null && (
        <div className="composer">
          <div className="composer-inner">
            <UserInputCard request={turn.pendingInput} onResolve={onResolveInput} />
          </div>
        </div>
      )}

      <Composer
        busy={busy}
        controls={<ModelControls choice={choice} />}
        quickPrompts={quickPrompts}
        inputBudget={
          choice.profile === null
            ? null
            : choice.profile.context_window - choice.profile.output_token_reserve
        }
        onSend={send_}
        onStop={cancel}
      />
    </div>
  )
}

/** Why a conversation could not be opened, in words that say what to do next. */
function describeLoadFailure(cause: unknown): string {
  if (cause instanceof Error && cause.message !== '') {
    return cause.message
  }
  return '打不开这个对话。'
}

/** Whether a message on its way to the server is already in the history. */
function alreadyStored(messages: Message[], pending: { text: string } | null): boolean {
  return (
    pending !== null &&
    pending.text !== '' &&
    messages.some((message) => message.role === 'user' && message.content === pending.text)
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
