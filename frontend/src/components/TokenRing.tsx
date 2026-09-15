/**
 * The draft's budget, drawn as a ring next to the send button.
 *
 * It exists to be ignored: a short reply against a large window leaves the
 * ring almost empty, and that is the point - the sliver is the honest size of
 * the draft, and the ring only starts meaning anything when a long paste or a
 * stack of screenshots begins to crowd the window. The colour carries the
 * verdict, the hover tooltip carries the number, and the screen stays quiet.
 *
 * Fed entirely from the client: the draft's estimate comes from
 * `api/tokens.ts` and the budget from the conversation's effective profile,
 * so nothing here waits on the server to say where the draft stands.
 */

import { ringState } from '../api/tokens'

const RADIUS = 8
const CIRCUMFERENCE = 2 * Math.PI * RADIUS

const COLORS = {
  ok: 'var(--accent-strong)',
  warn: 'var(--warn)',
  over: 'var(--bad-strong)',
} as const

export function TokenRing({ tokens, budget }: { tokens: number; budget: number }) {
  const share = budget > 0 ? Math.min(tokens / budget, 1) : 1
  const state = ringState(tokens, budget)
  const label = `输入约 ${Math.round(tokens).toLocaleString()} tokens`
  return (
    <svg
      className="token-ring"
      width={20}
      height={20}
      viewBox="0 0 20 20"
      role="img"
      aria-label={label}
    >
      <title>{label}</title>
      <circle className="token-ring-track" cx={10} cy={10} r={RADIUS} />
      <circle
        className="token-ring-fill"
        cx={10}
        cy={10}
        r={RADIUS}
        stroke={COLORS[state]}
        strokeDasharray={`${CIRCUMFERENCE * share} ${CIRCUMFERENCE}`}
        transform="rotate(-90 10 10)"
      />
    </svg>
  )
}
