/**
 * Routing and the one gate that matters.
 *
 * `GET /api/health` is the only route the backend answers before a data
 * directory has been chosen, so it is what decides whether the app shows the
 * setup screen or the chat. Any request that comes back `setup_required` flips
 * the same switch, in case the directory goes away underneath a running app.
 */

import { useCallback, useEffect, useState } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate, useParams } from 'react-router'

import { checkHealth, onSetupRequired } from './api/client'
import { Sidebar } from './components/Sidebar'
import { useConfirm } from './hooks/useConfirm'
import { useConversations } from './hooks/useConversations'
import { useToast } from './hooks/useToast'
import { ChatPage } from './pages/ChatPage'
import { KnowledgePage } from './pages/KnowledgePage'
import { MemoryPage } from './pages/MemoryPage'
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
    return (
      <SetupPage
        configured={false}
        onConfigured={() => setGate({ state: 'ok', configured: true })}
      />
    )
  }
  return <Shell onConfigured={() => setGate({ state: 'ok', configured: true })} />
}

function Shell({ onConfigured }: { onConfigured(): void }) {
  const conversations = useConversations()
  const confirm = useConfirm()
  const toast = useToast()
  // Subscribes the shell to navigation, so the highlighted row follows the URL.
  const location = useLocation()
  const navigate = useNavigate()
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem(COLLAPSED_KEY) === '1')
  const matched = /^\/c\/([^/]+)/.exec(location.pathname)
  // Where the settings screen's close button returns to. Remembered here rather
  // than taken from browser history: opening the app straight onto /setup leaves
  // no earlier page *inside* the app, and going back would leave it entirely.
  const [backTo, setBackTo] = useState('/')

  useEffect(() => {
    // Both full-screen pages are excluded: what came before either one is what
    // its close button goes back to.
    if (
      location.pathname !== '/setup' &&
      location.pathname !== '/memories' &&
      location.pathname !== '/knowledge'
    ) {
      setBackTo(location.pathname)
    }
  }, [location.pathname])

  function toggleSidebar() {
    setCollapsed((current) => {
      const next = !current
      localStorage.setItem(COLLAPSED_KEY, next ? '1' : '0')
      return next
    })
  }

  async function remove(conversation: { id: string; title: string }) {
    const confirmed = await confirm.ask({
      title: '把这段对话移到回收站？',
      body: `「${conversation.title}」会被移到回收站，之后可以恢复。`,
      confirmLabel: '移到回收站',
      danger: true,
    })
    if (!confirmed) {
      return
    }
    try {
      await conversations.remove(conversation.id)
      // The row disappearing is the result; where it went is not on screen.
      toast.show('已移到回收站')
    } catch {
      // This used to be an unhandled rejection: the row stayed where it was and
      // nothing on screen said why.
      toast.show('没能删掉这个对话，看看后端是不是在运行。', 'bad')
    }
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
          {/* The shell only exists once the app is configured, which is what
              tells the settings screen that there is no first step left to do. */}
          <Route
            path="/setup"
            element={
              <SetupPage configured onClose={() => navigate(backTo)} onConfigured={onConfigured} />
            }
          />
          <Route path="/" element={<NewChatPage conversations={conversations} />} />
          <Route
            path="/c/:conversationId"
            element={<ConversationRoute conversations={conversations} />}
          />
          <Route path="/memories" element={<MemoryPage onClose={() => navigate(backTo)} />} />
          <Route path="/knowledge" element={<KnowledgePage onClose={() => navigate(backTo)} />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
      {confirm.dialog}
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
