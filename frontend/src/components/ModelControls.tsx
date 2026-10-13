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
 *
 * The dropdown is one section per profile: which configuration answers and
 * which of its models are halves of one pick, and a model name means nothing
 * off the endpoint that listed it - so each row carries its profile's id, and
 * picking from another section switches to that profile as well as naming the
 * model. The section header is also the only place on screen that says which
 * configuration is answering right now.
 */

import type { ThinkingLevel } from '../api/thinking'
import { THINKING_LABELS, THINKING_LEVELS } from '../api/thinking'
import { modelOptionId, parseModelOptionId, type ModelChoice } from '../hooks/useModelChoice'
import { Menu } from './Menu'

export function ModelControls({ choice }: { choice: ModelChoice }) {
  const { profile, groups, model, setModel, level, setLevel } = choice

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
      title={`当前配置：${profile.name}\n当前模型：${model}\n思考强度：${THINKING_LABELS[level]}`}
      items={groups.flatMap((group) =>
        group.models.map((name) => ({
          id: modelOptionId(group.profileId, name),
          label: name,
          group: group.label,
        })),
      )}
      selected={modelOptionId(profile.id, model)}
      onPick={(id) => {
        const pick = parseModelOptionId(id)
        if (pick !== null) {
          setModel(pick.profileId, pick.model)
        }
      }}
      footer={<ThinkSegments level={level} setLevel={setLevel} />}
    />
  )
}

/** The six stops, as one segmented row at the foot of the panel. */
function ThinkSegments({
  level,
  setLevel,
}: {
  level: ThinkingLevel
  setLevel(level: ThinkingLevel): void
}) {
  return (
    // One segment per stop, and the position is the setting: no fill to read,
    // no thumb to land, and a strength is one click however far away it sits -
    // a dial's six stops were getting one click too many to reach by drag.
    // Drawn for every profile rather than only for the ones that describe
    // thinking: the strengths are a field this app sets itself, so there is
    // nothing a profile could have failed to configure - and a control whose
    // right-hand segments did nothing would be a worse lie than one that is
    // honestly at its leftmost stop.
    <div className="think-segments" role="radiogroup" aria-label="思考强度">
      <span className="think-segments-label">思考</span>
      <div className="think-segments-track">
        {THINKING_LEVELS.map((each) => (
          <button
            key={each}
            type="button"
            role="radio"
            aria-checked={each === level}
            className={`think-segment${each === level ? ' is-on' : ''}`}
            onClick={() => setLevel(each)}
          >
            {THINKING_LABELS[each]}
          </button>
        ))}
      </div>
    </div>
  )
}
