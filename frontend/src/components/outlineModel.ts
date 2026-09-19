/**
 * The model behind the outline rail, kept apart from the component so the
 * fast-refresh boundary stays clean and the rules test without a DOM.
 */

export interface OutlineEntry {
  element: HTMLElement
  label: string
  level: 1 | 2
}

/** Which `data-outline` values become entries, and at what depth. */
export function outlineLevel(value: string): 1 | 2 | null {
  if (value === 'user') {
    return 1
  }
  if (value === 'h2' || value === 'h3') {
    return 2
  }
  return null
}

/** The hover label: one collapsed line of the text, cut off when it runs long. */
export function outlineLabel(text: string, max = 48): string {
  const single = text.replace(/\s+/g, ' ').trim()
  return single.length > max ? `${single.slice(0, max)}…` : single
}

/** The sections of a scroll container, in document order. */
export function collectOutline(root: ParentNode): OutlineEntry[] {
  const entries: OutlineEntry[] = []
  root.querySelectorAll<HTMLElement>('[data-outline]').forEach((element) => {
    const level = outlineLevel(element.dataset.outline ?? '')
    if (level !== null) {
      entries.push({ element, level, label: outlineLabel(element.textContent ?? '') })
    }
  })
  return entries
}

/**
 * Which section the viewport is in: the last one whose top has climbed past
 * the reading line. None have, before the first scroll - the conversation
 * starts at its top, and the first entry owns that.
 */
export function activeIndex(tops: number[], readingLine: number): number {
  let current = 0
  for (let index = 0; index < tops.length; index += 1) {
    if (tops[index] <= readingLine) {
      current = index
    }
  }
  return tops.length === 0 ? -1 : current
}
