/**
 * Routing and the one gate that matters.
 *
 * `GET /api/health` is the only route the backend answers before a data
 * directory has been chosen, so it is what decides whether the app shows the
 * setup screen or the chat. Any request that comes back `setup_required` flips
 * the same switch, in case the directory goes away underneath a running app.
 */

import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate, useParams } from 'react-router'

import { checkHealth, onSetupRequired } from './api/client'
import { Sidebar } from './components/Sidebar'
import { WindowControls } from './components/WindowControls'
import { useConfirm } from './hooks/useConfirm'
import { useConversations } from './hooks/useConversations'
import { useToast } from './hooks/useToast'
import { ChatPage } from './pages/ChatPage'
import { KnowledgePage } from './pages/KnowledgePage'
import { MemoryPage } from './pages/MemoryPage'
import { NewChatPage } from './pages/NewChatPage'
import { SetupPage } from './pages/SetupPage'
import { PopupWindow } from './plugins/PopupWindow'
import {
  SHELL_NAVIGATE_EVENT,
  SHELL_NOTICE_EVENT,
  installShellBridge,
  toggleMaximize,
} from './plugins/shell'
import { usePluginFrontends } from './plugins/registry'

const COLLAPSED_KEY = 'jarvis.sidebar.collapsed'

type Gate = { state: 'checking' } | { state: 'offline' } | { state: 'ok'; configured: boolean }

/** 桌面壳弹窗窗口的路径：/popup/<pluginId>，插件 id 的字符集在 host 里钉死。 */
const POPUP_ROUTE = /^\/popup\/([a-z0-9-]+)$/

export function App() {
  const [gate, setGate] = useState<Gate>({ state: 'checking' })
  const location = useLocation()

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

  // The desktop shell's bridge exists per page - the popup windows need it
  // as much as the main one, and this is the one component every URL loads.
  useEffect(() => {
    installShellBridge()
  }, [])

  // 弹窗窗口的路径：过了 gate 就只渲染插件的 quickCapture，没有外壳。
  // 未配置时桌面壳注册不出任何热键，弹窗窗口到不了这里，防御性给空白。
  const popupMatch = POPUP_ROUTE.exec(location.pathname)

  // 除弹窗外的一切状态都套上 app-root：主窗口无边框之后，拖拽区和窗口
  // 按钮只有标题条提供，它必须在每个页面（含首次运行的全屏设置页）之上。
  if (gate.state === 'checking') {
    return (
      <AppRoot>
        <div className="pywebview-drag-region empty-state" onDoubleClick={toggleMaximize}>
          正在连接 Jarvis…
        </div>
      </AppRoot>
    )
  }
  if (gate.state === 'offline') {
    return (
      <AppRoot>
        <div className="pywebview-drag-region empty-state" onDoubleClick={toggleMaximize}>
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
      </AppRoot>
    )
  }
  if (popupMatch !== null) {
    if (!gate.configured) {
      return null
    }
    return <PopupWindow pluginId={popupMatch[1]} />
  }
  if (!gate.configured) {
    return (
      <AppRoot>
        <SetupPage
          configured={false}
          onConfigured={() => setGate({ state: 'ok', configured: true })}
        />
      </AppRoot>
    )
  }
  return (
    <AppRoot>
      <Shell onConfigured={() => setGate({ state: 'ok', configured: true })} />
    </AppRoot>
  )
}

/** 一列纵排的外壳：窗口控制是右上角的悬浮层（不占布局高度），页面内容
 *  占满全部。弹窗不进来。 */
function AppRoot({ children }: { children: ReactNode }) {
  return (
    <div className="app-root">
      <WindowControls />
      {children}
    </div>
  )
}

function Shell({ onConfigured }: { onConfigured(): void }) {
  const conversations = useConversations()
  const confirm = useConfirm()
  const toast = useToast()
  const { frontends: plugins, ready: pluginsReady } = usePluginFrontends()
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
    // The desktop shell talks to the main window through these: 唤出整页插件
    // 时请求一次 SPA 跳转（整页刷新会把会话缓存丢掉），热键冲突之类的提示
    // 则借道 toast。浏览器里这两个事件永远不来，监听是无害的空挂。
    function onNavigate(event: Event) {
      navigate((event as CustomEvent<string>).detail)
    }
    function onNotice(event: Event) {
      toast.show((event as CustomEvent<string>).detail, 'bad')
    }
    window.addEventListener(SHELL_NAVIGATE_EVENT, onNavigate)
    window.addEventListener(SHELL_NOTICE_EVENT, onNotice)
    return () => {
      window.removeEventListener(SHELL_NAVIGATE_EVENT, onNavigate)
      window.removeEventListener(SHELL_NOTICE_EVENT, onNotice)
    }
  }, [navigate, toast])

  useEffect(() => {
    // Every full-screen page is excluded: what came before it is what its close
    // button goes back to. Plugins' pages belong in that company - their paths
    // come from the plugins' own meta. The wait for `pluginsReady` matters: on
    // a fresh load of /notes the registry is still empty, the exclusion list
    // would not name it, and /notes would be remembered as its own close
    // target - the close button would then navigate to where it already is.
    if (!pluginsReady) {
      return
    }
    const pluginPaths = plugins.map((plugin) => plugin.meta.navPath)
    if (
      location.pathname !== '/setup' &&
      location.pathname !== '/memories' &&
      location.pathname !== '/knowledge' &&
      !pluginPaths.includes(location.pathname)
    ) {
      setBackTo(location.pathname)
    }
  }, [location.pathname, plugins, pluginsReady])

  function toggleSidebar() {
    setCollapsed((current) => {
      const next = !current
      localStorage.setItem(COLLAPSED_KEY, next ? '1' : '0')
      return next
    })
  }

  async function remove(conversation: { id: string; title: string }) {
    const confirmed = await confirm.ask({
      title: '删除这段对话？',
      body: `「${conversation.title}」会被永久删除，消息、执行记录和点赞点踩一起删掉，无法恢复。`,
      confirmLabel: '永久删除',
      danger: true,
    })
    if (!confirmed) {
      return
    }
    try {
      await conversations.remove(conversation.id)
      // The row disappearing is the result; where it went is not on screen.
      toast.show('已删除')
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
        search={conversations.search}
        onSearchChange={conversations.setSearch}
        plugins={plugins.map((plugin) => ({
          id: plugin.id,
          label: plugin.meta.navLabel,
          path: plugin.meta.navPath,
          Icon: plugin.meta.navIcon,
        }))}
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
          {/* Each plugin's page is the plugin's own component on its own path;
              onClose behaves like the other full-screen pages' close buttons. */}
          {plugins.map((plugin) => (
            <Route
              key={plugin.id}
              path={plugin.meta.navPath}
              element={<plugin.Page onClose={() => navigate(backTo)} />}
            />
          ))}
          {/* The catch-all waits for the plugin registry: redirecting before
              the plugin routes exist would bounce a deep link like /notes
              home on every fresh load. */}
          {pluginsReady && <Route path="*" element={<Navigate to="/" replace />} />}
        </Routes>
      </main>
      {/* The popups plugins summon globally - a hotkey-driven quick note, for
          one - live at the shell so they work on every page. */}
      {plugins.map((plugin) =>
        plugin.quickCapture === undefined ? null : <plugin.quickCapture key={plugin.id} />,
      )}
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
