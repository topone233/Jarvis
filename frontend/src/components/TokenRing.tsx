/**
 * How much room the conversation has left before the model's context fills up.
 *
 * The numbers come from the `context.ready` event, which the backend emits with
 * real estimates rather than guesses. `total` is the input budget of whichever
 * model profile the conversation uses.
 */

const RADIUS = 12.5
const CIRCUMFERENCE = 2 * Math.PI * RADIUS

export function TokenRing({
  remaining,
  total,
}: {
  remaining: number | null
  total: number | null
}) {
  if (total === null || total <= 0) {
    return null
  }
  const left = Math.max(0, Math.min(remaining ?? total, total))
  const fraction = left / total
  const percent = Math.round(fraction * 100)

  return (
    <span
      className="token-ring"
      title={`上下文剩余约 ${left.toLocaleString('zh-CN')} / ${total.toLocaleString('zh-CN')} tokens`}
    >
      <svg viewBox="0 0 30 30" width={30} height={30} aria-hidden="true">
        <circle className="track" cx={15} cy={15} r={RADIUS} strokeWidth={2.5} />
        <circle
          className="value"
          cx={15}
          cy={15}
          r={RADIUS}
          strokeWidth={2.5}
          strokeLinecap="round"
          strokeDasharray={CIRCUMFERENCE}
          strokeDashoffset={CIRCUMFERENCE * (1 - fraction)}
        />
      </svg>
      <span className="percent">{percent}</span>
    </span>
  )
}
