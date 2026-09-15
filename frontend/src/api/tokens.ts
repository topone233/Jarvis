/**
 * The draft's cost, estimated the same way the backend estimates everything.
 *
 * The ring by the send button reads from here, and the backend's compaction
 * counts images with the same constant - if the two ever drift apart, the
 * budget the composer shows and the budget the server enforces stop being
 * the same budget. The algorithm mirrors `backend/app/tokens.py` exactly:
 * CJK characters cost more than the rest, and an empty string still counts
 * as one, which is the backend's floor.
 */

/** What one pasted image costs, roughly. Kept identical to the backend's. */
export const IMAGE_TOKEN_ESTIMATE = 1_000

export function estimateTokens(value: string): number {
  let cjkCount = 0
  for (const character of value) {
    if (character >= '一' && character <= '鿿') {
      cjkCount += 1
    }
  }
  const otherCount = value.length - cjkCount
  return Math.max(1, Math.ceil(cjkCount * 1.2 + otherCount / 4))
}

/** The whole draft: what was typed, plus one constant per pasted image. */
export function draftTokens(text: string, imageCount: number): number {
  const textTokens = text.trim() === '' ? 0 : estimateTokens(text)
  return textTokens + imageCount * IMAGE_TOKEN_ESTIMATE
}

/** What the ring's colour says: ordinary, nearly full, or over the budget. */
export function ringState(tokens: number, budget: number): 'ok' | 'warn' | 'over' {
  if (tokens >= budget) {
    return 'over'
  }
  if (tokens >= budget * 0.8) {
    return 'warn'
  }
  return 'ok'
}
