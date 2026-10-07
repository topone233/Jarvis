/**
 * The input box.
 *
 * Enter sends and Shift+Enter breaks the line, except while an input method is
 * composing - which is not a corner case in Chinese, it is how every message
 * gets typed. Without that guard, picking a candidate with Enter would send a
 * half-finished word.
 *
 * Paste only steals image items: a screenshot goes to the thumbnail row inside
 * the card, while plain text - including the text of a mixed clipboard - goes
 * through untouched. Images are compressed the moment they arrive, so the
 * preview is exactly what will be sent and stored.
 *
 * The card is one row - attach, draft, model and send side by side - with any
 * pasted images waiting in a strip above it. The draft grows from a single
 * line up to the ceiling and scrolls past it, so multi-line pastes and
 * Shift+Enter still work inside a box that reads as a single-line field. The
 * paperclip opens the same file picker a paste would bypass, so attaching is
 * possible without a clipboard round-trip.
 */

import {
  useLayoutEffect,
  useRef,
  useState,
  type ChangeEvent,
  type ClipboardEvent,
  type KeyboardEvent,
  type ReactNode,
} from 'react'

import { compressImage } from '../api/images'
import { draftTokens } from '../api/tokens'
import type { QuickPrompt } from '../api/types'
import { TokenRing } from './TokenRing'
import { PaperclipIcon, SendIcon, StopIcon } from './icons'

export interface ComposerProps {
  /** True while an answer is being produced, which turns the send button into a
   *  stop button. Left false where there is nothing to stop. */
  busy?: boolean
  /** The controls the page wants in the toolbar row - the model, the thinking
   *  dial. The composer decides where they go, not what they are. */
  controls?: ReactNode
  /** The buttons above an empty box: one click fills the prompt in for the
   *  user to finish. Absent or empty means no row - which is also what an
   *  explicitly emptied list stores, so it needs no special case. */
  quickPrompts?: QuickPrompt[]
  /** The effective profile's input budget (context window minus reserve), for
   *  the ring. Null when no profile is configured, and then there is no ring. */
  inputBudget?: number | null
  /** The page observes the card's height (the turn rail's visible band and the
   *  back-to-bottom button both hang above it). */
  boxRef?: (element: HTMLDivElement | null) => void
  onSend(text: string, images: string[]): void
  onStop?: () => void
}

const MAX_HEIGHT = 320

export function Composer({
  busy = false,
  controls,
  quickPrompts,
  inputBudget = null,
  boxRef,
  onSend,
  onStop,
}: ComposerProps) {
  const ref = useRef<HTMLTextAreaElement | null>(null)
  const fileRef = useRef<HTMLInputElement | null>(null)
  // The textarea stays uncontrolled - the height is set from its own
  // scrollHeight and never from React state - but the text keeps a state
  // mirror, because the ring needs the draft on every keystroke and a ref
  // read during render is a warning with good reasons. The re-render per
  // keystroke is the cost; this component is small enough to pay it.
  // Whitespace does not count, which is the same rule `submit` refuses on.
  const [draft, setDraft] = useState('')
  // The pasted images, already compressed, as data URLs: what the previews
  // show is what the send button puts on the wire and what gets stored.
  const [images, setImages] = useState<string[]>([])
  const [pasteNote, setPasteNote] = useState<string | null>(null)

  // Grow with the text up to a ceiling, then scroll inside the box.
  const fit = () => {
    const element = ref.current
    if (element === null) {
      return
    }
    element.style.height = 'auto'
    element.style.height = `${Math.min(element.scrollHeight, MAX_HEIGHT)}px`
  }

  useLayoutEffect(fit)

  function changed() {
    fit()
    const element = ref.current
    if (element !== null) {
      setDraft(element.value)
    }
  }

  function submit() {
    const element = ref.current
    if (element === null || busy) {
      return
    }
    const text = element.value.trim()
    if (text === '' && images.length === 0) {
      return
    }
    element.value = ''
    fit()
    setDraft('')
    setImages([])
    setPasteNote(null)
    onSend(text, images)
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== 'Enter' || event.shiftKey) {
      return
    }
    if (event.nativeEvent.isComposing) {
      return
    }
    event.preventDefault()
    submit()
  }

  // Only image items are intercepted; a clipboard that mixes text and images
  // loses nothing, because the text keeps going through natively.
  async function onPaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    const files: File[] = []
    for (const item of event.clipboardData?.items ?? []) {
      if (item.kind === 'file' && item.type.startsWith('image/')) {
        const file = item.getAsFile()
        if (file !== null) {
          files.push(file)
        }
      }
    }
    if (files.length === 0) {
      return
    }
    event.preventDefault()
    await attach(files)
  }

  // The paperclip and the paste funnel into the same store: one code path for
  // reading and compressing, one place for the failure note.
  async function attach(files: File[]) {
    const pasted: string[] = []
    for (const file of files) {
      try {
        pasted.push(await compressImage(file))
      } catch {
        setPasteNote('这张图片没法读取。')
      }
    }
    if (pasted.length > 0) {
      setPasteNote(null)
      setImages((current) => [...current, ...pasted])
    }
  }

  function onPicked(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? [])
    event.target.value = ''
    if (files.length > 0) {
      void attach(files)
    }
  }

  // A quick prompt is a starting point, not a send: fill the box and put the
  // caret there, then let the user finish the thought. The draft turning
  // non-empty is what takes the row away - the same rule that showed it.
  function fillWith(prompt: string) {
    const element = ref.current
    if (element === null) {
      return
    }
    element.value = prompt
    fit()
    setDraft(prompt)
    element.focus()
  }

  const filled = draft.trim() !== ''
  const hasDraft = filled || images.length > 0
  const tokens = draftTokens(draft, images.length)

  return (
    <div className="composer">
      <div className="composer-inner">
        {!hasDraft && quickPrompts !== undefined && quickPrompts.length > 0 && (
          <div className="composer-quick">
            {quickPrompts.map((item, index) => (
              <button
                key={`${index}:${item.name}`}
                type="button"
                className="quick-prompt"
                onClick={() => fillWith(item.prompt)}
              >
                {item.name}
              </button>
            ))}
          </div>
        )}
        <div className="composer-box" ref={boxRef}>
          {images.length > 0 && (
            <div className="composer-images">
              {images.map((image, index) => (
                <span className="composer-image" key={`${index}:${image.slice(-16)}`}>
                  <img src={image} alt={`待发送图片 ${index + 1}`} />
                  <button
                    type="button"
                    className="composer-image-remove"
                    title="移除这张图片"
                    aria-label={`移除第 ${index + 1} 张图片`}
                    onClick={() => setImages((current) => current.filter((_, at) => at !== index))}
                  >
                    ✕
                  </button>
                </span>
              ))}
              {pasteNote !== null && <span className="composer-image-note">{pasteNote}</span>}
            </div>
          )}
          <div className="composer-row">
            <div className="composer-tools">
              <button
                type="button"
                className="icon-button"
                title="添加图片"
                onClick={() => fileRef.current?.click()}
              >
                <PaperclipIcon size={17} />
              </button>
              <input
                ref={fileRef}
                type="file"
                accept="image/*"
                multiple
                className="composer-file-input"
                onChange={onPicked}
              />
            </div>
            <textarea
              ref={ref}
              rows={1}
              placeholder="给 Jarvis 发消息"
              onChange={changed}
              onPaste={(event) => void onPaste(event)}
              onKeyDown={onKeyDown}
            />
            <div className="composer-side">
              {controls}
              {hasDraft && inputBudget !== null && inputBudget > 0 && (
                <TokenRing tokens={tokens} budget={inputBudget} />
              )}
              {busy && onStop ? (
                <button type="button" className="stop-button" title="停止生成" onClick={onStop}>
                  <StopIcon size={16} />
                </button>
              ) : (
                <button
                  type="button"
                  className={hasDraft ? 'send-button is-filled' : 'send-button'}
                  title="发送"
                  onClick={submit}
                >
                  <SendIcon size={17} />
                </button>
              )}
            </div>
          </div>
        </div>
        <div className="composer-hint">Enter 发送，Shift + Enter 换行</div>
      </div>
    </div>
  )
}
