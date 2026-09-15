/** The starting screen: an empty page with the composer in the middle. */

import { useState } from 'react'
import { useNavigate } from 'react-router'

import { Composer } from '../components/Composer'
import { ModelControls } from '../components/ModelControls'
import type { ConversationsController } from '../hooks/useConversations'
import { useModelChoice } from '../hooks/useModelChoice'
import { useQuickPrompts } from '../hooks/useQuickPrompts'
import { setPendingImages } from '../pendingImages'

export function NewChatPage({ conversations }: { conversations: ConversationsController }) {
  const navigate = useNavigate()
  const [failed, setFailed] = useState(false)
  // No conversation yet, so there is no profile pinned to one: this is the
  // default the conversation about to be created will not override.
  const choice = useModelChoice(null)
  const quickPrompts = useQuickPrompts()

  async function start(text: string, images: string[]) {
    setFailed(false)
    // The conversation is created here rather than on the first keystroke, so
    // clicking "新对话" and wandering off does not leave an empty shell behind.
    const conversation = await conversations.create()
    if (conversation === null) {
      setFailed(true)
      return
    }
    // The images cross routes through the in-memory handoff - they are data
    // URLs, far too big for the history entry the text travels in.
    if (images.length > 0) {
      setPendingImages(images)
    }
    // The choice rides along with the first message. Without it, a first message
    // sent with another model showing would be answered by the profile's own.
    navigate(`/c/${conversation.id}`, {
      state: { firstMessage: text, chatModel: choice.model, level: choice.level },
    })
  }

  return (
    <div className="hero">
      <div className="hero-inner">
        <h1 className="hero-title">有什么可以帮你的？</h1>
        <p className="hero-subtitle">问点什么，或者贴一段代码。</p>
        <Composer
          controls={<ModelControls choice={choice} />}
          quickPrompts={quickPrompts}
          inputBudget={
            choice.profile === null
              ? null
              : choice.profile.context_window - choice.profile.output_token_reserve
          }
          onSend={(text, images) => void start(text, images)}
        />
        {failed && <div className="form-error">没能新建对话，看看后端是不是在运行。</div>}
      </div>
    </div>
  )
}
