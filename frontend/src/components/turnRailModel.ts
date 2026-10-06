/**
 * The model behind the turn rail, kept apart from the component so the
 * fast-refresh boundary stays clean and the rules test without a DOM.
 *
 * One tick per user message - a turn starts where the user speaks. The answer
 * a tick stands for is whatever follows it in the container, which for a live
 * conversation is the sibling `.turn` and for a finished one the next user
 * bubble's predecessor.
 */

export interface TurnMark {
  element: HTMLElement
  /** The user's words, collapsed to one line. */
  prompt: string
  /** The answer that followed, collapsed to three lines' worth. */
  response: string
}

/** The hover label: a collapsed line of the text, cut off when it runs long. */
export function collapseLine(text: string, max: number): string {
  const single = text.replace(/\s+/g, ' ').trim()
  return single.length > max ? `${single.slice(0, max)}…` : single
}

/**
 * The turns of a scroll container, in document order.
 *
 * A user bubble is marked `data-outline="user"` by the list; the answer text
 * is read from the `.markdown` blocks of the siblings between it and the next
 * user bubble. Only the prose qualifies: a turn also carries the progress
 * strip, the thinking trail and the action row, and a preview that opens
 * "整理上下文 0.04 秒…" is quoting the machinery, not the answer.
 */
export function collectTurns(root: ParentNode): TurnMark[] {
  const marks: TurnMark[] = []
  const users = root.querySelectorAll<HTMLElement>('[data-outline="user"]')
  users.forEach((element) => {
    let response = ''
    let node = element.nextElementSibling
    while (node !== null && !(node instanceof HTMLElement && node.dataset.outline === 'user')) {
      const bodies =
        node instanceof HTMLElement && node.classList.contains('markdown')
          ? [node]
          : [...node.querySelectorAll<HTMLElement>('.markdown')]
      bodies.forEach((body) => {
        response += ` ${body.textContent ?? ''}`
      })
      node = node.nextElementSibling
    }
    marks.push({
      element,
      prompt: collapseLine(element.textContent ?? '', 48),
      response: collapseLine(response, 160),
    })
  })
  return marks
}

/**
 * Which turn the viewport is in: the last one whose top has climbed past
 * the reading line. None have, before the first scroll - the conversation
 * starts at its top, and the first turn owns that.
 */
export function activeTurnIndex(tops: number[], readingLine: number): number {
  let current = 0
  for (let index = 0; index < tops.length; index += 1) {
    if (tops[index] <= readingLine) {
      current = index
    }
  }
  return tops.length === 0 ? -1 : current
}
