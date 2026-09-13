/**
 * What the composer offers besides text: which model, and how hard it should
 * think before answering.
 *
 * Both in one panel, because they are one question - how this run should be
 * answered - and two buttons beside each other in a row that also holds a
 * textarea is a row with no room left for typing.
 *
 * Drawn by the page rather than by `Composer` itself, so the composer stays the
 * box and the keyboard rules and nothing else. Both pages that have a composer
 * draw the same controls, so they are in one place.
 */

import type { CSSProperties } from 'react'

import { levelAt, levelIndex, THINKING_LABELS, THINKING_LEVELS } from '../api/thinking'
import type { ThinkingLevel } from '../api/thinking'
import type { ModelChoice } from '../hooks/useModelChoice'
import { Menu } from './Menu'

const LAST = THINKING_LEVELS.length - 1

export function ModelControls({ choice }: { choice: ModelChoice }) {
  const { profile, models, model, setModel, level, setLevel } = choice

  if (profile === null) {
    // Nothing configured yet, so there is no model to name and nothing to pick
    // from. An empty button would be a control that cannot do anything.
    return null
  }

  return (
    <Menu
      // The model name alone while the dial is at 关: that is the profile's own
      // answer, and the button would otherwise carry a word about a setting
      // nobody made. Off 关 the run is being told something, so it says so.
      label={level === 'off' ? model : `${model} · ${THINKING_LABELS[level]}`}
      title={`当前模型：${model}\n思考强度：${THINKING_LABELS[level]}`}
      items={models.map((name) => ({ id: name, label: name }))}
      selected={model}
      onPick={setModel}
      footer={<ThinkDial level={level} setLevel={setLevel} />}
    />
  )
}

/** The four stops, as one row at the foot of the panel. */
function ThinkDial({
  level,
  setLevel,
}: {
  level: ThinkingLevel
  setLevel(level: ThinkingLevel): void
}) {
  return (
    // A range input and not four rows of the list: the stops are one setting
    // with an order, and a slider says that where a column of words does not.
    // It also comes with arrow keys, Home/End and touch, and `aria-valuetext`
    // is what reads the stop instead of a number.
    //
    // Drawn for every profile rather than only for the ones that describe
    // thinking. The three strengths are a field this app sets itself, so there
    // is nothing a profile could have failed to configure - and a dial whose
    // right-hand three quarters did nothing would be a worse lie than one that
    // is honestly at its leftmost stop.
    <div
      className={level === 'off' ? 'think-dial' : 'think-dial is-on'}
      style={{ '--filled': levelIndex(level) / LAST } as CSSProperties}
      title={`思考强度：${THINKING_LEVELS.map((each) => THINKING_LABELS[each]).join(' / ')}`}
    >
      <span className="think-dial-label">思考</span>
      <input
        type="range"
        className="think-slider"
        min={0}
        max={LAST}
        step={1}
        value={levelIndex(level)}
        aria-label="思考强度"
        aria-valuetext={THINKING_LABELS[level]}
        onChange={(event) => setLevel(levelAt(Number(event.target.value)))}
      />
      <span className="think-dial-value">{THINKING_LABELS[level]}</span>
    </div>
  )
}
