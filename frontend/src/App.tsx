/**
 * Routing and the one gate that matters.
 *
 * `GET /api/health` is the only route the backend answers before a data
 * directory has been chosen, so it is what decides whether the app shows the
 * setup screen or the chat. Any request that comes back `setup_required` flips
 * the same switch, in case the directory goes away underneath a running app.
 */

import { useCallback, useEffect, useState } from 'react'
import { Navigate, Route, Routes, useLocation, useParams } from 'react-router'

import { checkHealth, onSetupRequired } from './api/client'
import { Sidebar } from './components/Sidebar'
import { useConversations } from './hooks/useConversations'
import { ChatPage } from './pages/ChatPage'
import { NewChatPage } from './pages/NewChatPage'
import { SetupPage } from './pages/SetupPage'

const COLLAPSED_KEY = 'jarvis.sidebar.collapsed'

type Gate = { state: 'checking' } | { state: 'offline' } | { state: 'ok'; configured: boolean }

export function App() {
  const [gate, setGate] = useState<Gate>({ state: 'checking' })

  const refreshHealth = useCallback(async () => {
    try {
      const health = await checkHealth()
      setGate({ state: 'ok', configured: health.configured })
    } catch {
      setGate({ state: 'offline' })
    }
  }, [])

  useEffect(() => {
    void refreshHealth()
  }, [refreshHealth])

  useEffect(() => {
    onSetupRequired(() => setGate({ state: 'ok', configured: false }))
    return () => onSetupRequired(null)
  }, [])

  if (gate.state === 'checking') {
    return <div className="empty-state">正在连接 Jarvis…</div>
  }
  if (gate.state === 'offline') {
    return (
      <div className="empty-state">
        <div>
          <p>连不上 Jarvis 服务。</p>
          <button
            type="button"
            className="button button-ghost"
            onClick={() => void refreshHealth()}
          >
            重试
          </button>
        </div>
      </div>
    )
  }
  if (!gate.configured) {
    return <SetupPage onConfigured={() => setGate({ state: 'ok', configured: true })} />
  }
  return <Shell onConfigured={() => setGate({ state: 'ok', configured: true })} />
}

function Shell({ onConfigured }: { onConfigured(): void }) {
  const conversations = useConversations()
  // Subscribes the shell to navigation, so the highlighted row follows the URL.
  const location = useLocation()
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem(COLLAPSED_KEY) === '1')
  const matched = /^\/c\/([^/]+)/.exec(location.pathname)

  function toggleSidebar() {
    setCollapsed((current) => {
      const next = !current
      localStorage.setItem(COLLAPSED_KEY, next ? '1' : '0')
      return next
    })
  }

  function remove(conversation: { id: string; title: string }) {
    if (!window.confirm(`把「${conversation.title}」移到回收站？`)) {
      return
    }
    void conversations.remove(conversation.id)
  }

  return (
    <div className="app">
      <Sidebar
        conversations={conversations.conversations}
        activeId={matched ? matched[1] : null}
        collapsed={collapsed}
        onToggle={toggleSidebar}
        onDelete={remove}
      />
      <main className="main">
        <Routes>
          <Route path="/setup" element={<SetupPage onConfigured={onConfigured} />} />
          <Route path="/" element={<NewChatPage conversations={conversations} />} />
          <Route
            path="/c/:conversationId"
            element={<ConversationRoute conversations={conversations} />}
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  )
}

/**
 * The conversation page is keyed by its id so that switching conversations
 * builds a fresh one. Without that, the previous conversation's run - and its
 * open stream - would carry over into the next.
 */
function ConversationRoute({
  conversations,
}: {
  conversations: ReturnType<typeof useConversations>
}) {
  const { conversationId } = useParams()
  if (conversationId === undefined) {
    return <Navigate to="/" replace />
  }
  return (
    <ChatPage key={conversationId} conversationId={conversationId} conversations={conversations} />
  )
}
