/** The starting screen: an empty page with the composer in the middle. */

import { useState } from 'react'
import { useNavigate } from 'react-router'

import { Composer } from '../components/Composer'
import type { ConversationsController } from '../hooks/useConversations'

export function NewChatPage({ conversations }: { conversations: ConversationsController }) {
  const navigate = useNavigate()
  const [failed, setFailed] = useState(false)

  async function start(text: string) {
    setFailed(false)
    // The conversation is created here rather than on the first keystroke, so
    // clicking "新对话" and wandering off does not leave an empty shell behind.
    const conversation = await conversations.create()
    if (conversation === null) {
      setFailed(true)
      return
    }
    navigate(`/c/${conversation.id}`, { state: { firstMessage: text } })
  }

  return (
    <div className="hero">
      <div className="hero-inner">
        <h1 className="hero-title">有什么可以帮你的？</h1>
        <p className="hero-subtitle">问点什么，或者贴一段代码。</p>
        <Composer remaining={null} total={null} onSend={(text) => void start(text)} />
        {failed && <div className="form-error">没能新建对话，看看后端是不是在运行。</div>}
      </div>
    </div>
  )
}
