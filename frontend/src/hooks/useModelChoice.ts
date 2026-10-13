/**
 * The model and the thinking dial the composer is offering.
 *
 * One hook owns the profile list, because two things have to agree about it:
 * which configuration will answer, and which model names the dropdown can
 * offer.
 *
 * **The choice is global.** One `(profile, model, dial)` for the whole app,
 * remembered in localStorage: changing it once moves every conversation after
 * it - the one on screen, the others, and the ones not created yet - which is
 * what "改过一次模型之后，后续都用这个模型" means. The conversation's own
 * profile is only the fallback while the user has never picked anything (or
 * the pinned profile was deleted); the choice itself rides on every run
 * request, so the backend's `RunChoice` stays what it always was - per-request,
 * never configuration.
 *
 * **The dropdown crosses profiles.** Every profile gets a section carrying its
 * own model list, read live from its endpoint, and picking a name from another
 * section re-pins that profile with it - a model name is only meaningful on
 * the endpoint it was listed from. Without this, a pin could only ever be
 * re-pinned onto the profile it already named, and "设为默认" in settings
 * could not reach the composer at all.
 *
 * **"设为默认" outranks a pin on another profile.** Naming a new default is a
 * newer expression of intent than a pick made days ago, so the settings page
 * calls `resetChoiceOnDefault` and a pin pointing elsewhere is dropped. A pin
 * only the delete button could undo turned "设为默认" into a silent no-op for
 * every conversation - the exact shape of "设了默认，对话窗口还是旧接口".
 */

import { useEffect, useMemo, useState, useSyncExternalStore } from 'react'

import { listModelProfiles, listProfileModels } from '../api/endpoints'
import { effectiveDefault, modelNames } from '../api/profiles'
import { isThinkingLevel, type ThinkingLevel } from '../api/thinking'
import type { ModelProfile } from '../api/types'

/** What a choice consists of: which profile answers, which of its models, and where the dial is. */
export interface StoredChoice {
  /** The profile the pick pinned. Null says "never picked" - the conversation's own profile rules. */
  profileId: string | null
  /** Empty says "whatever the profile says". */
  chatModel: string
  level: ThinkingLevel
}

const STORAGE_KEY = 'jarvis.model-choice'

const DEFAULT_CHOICE: StoredChoice = { profileId: null, chatModel: '', level: 'off' }

function loadChoice(): StoredChoice {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (raw === null) {
      return DEFAULT_CHOICE
    }
    const parsed = JSON.parse(raw) as Partial<StoredChoice> | null
    if (parsed === null || typeof parsed !== 'object') {
      return DEFAULT_CHOICE
    }
    return {
      profileId: typeof parsed.profileId === 'string' ? parsed.profileId : null,
      chatModel: typeof parsed.chatModel === 'string' ? parsed.chatModel : '',
      level: isThinkingLevel(parsed.level) ? parsed.level : 'off',
    }
  } catch {
    // 读不回来（旧的或残缺的形状）就当没选过：composer 照常工作。
    return DEFAULT_CHOICE
  }
}

let current: StoredChoice = loadChoice()
const listeners = new Set<() => void>()

function setChoice(part: Partial<StoredChoice>): void {
  current = { ...current, ...part }
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(current))
  } catch {
    // 存不进去（隐私模式之类）只损失重启后的回退，本次会话的选择照常生效。
  }
  for (const listener of listeners) {
    listener()
  }
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

function getSnapshot(): StoredChoice {
  return current
}

/**
 * The choice once `defaultProfileId` has been named the default.
 *
 * A pin on a *different* profile would keep overriding the default with
 * nothing in the app able to undo it, so it is dropped, model name included:
 * the name belonged to the endpoint it was listed from. A pin on the same
 * profile survives - picking one of its models was a more specific wish than
 * flagging it as default, and flagging what already answers must not reset
 * that pick. The dial is not part of the question and rides along either way.
 */
export function choiceAfterDefault(
  current: StoredChoice,
  defaultProfileId: string,
): StoredChoice {
  if (current.profileId === null || current.profileId === defaultProfileId) {
    return current
  }
  return { ...current, profileId: null, chatModel: '' }
}

/** Applies `choiceAfterDefault` to the live choice. The settings page calls this once「设为默认」has saved. */
export function resetChoiceOnDefault(defaultProfileId: string): void {
  const next = choiceAfterDefault(current, defaultProfileId)
  if (next !== current) {
    setChoice(next)
  }
}

/** One dropdown section: a profile, and the model names it can be asked for. */
export interface ModelGroup {
  profileId: string
  /** The section header on screen: the profile's name, the default one marked. */
  label: string
  models: string[]
}

/**
 * The dropdown's sections, the default profile first.
 *
 * A profile whose endpoint would not say what it serves - the `/models` fetch
 * failed, or the list came back empty - still gets its section, offering the
 * profile's own model name: reaching a profile must not depend on its endpoint
 * cooperating. The section of the profile currently answering also carries the
 * pick itself even when the endpoint does not list it, because the name in use
 * has to stay selectable or the check would have nothing to sit on.
 */
export function buildModelGroups(
  profiles: ModelProfile[],
  listed: Record<string, string[]>,
  activeProfileId: string | null,
  currentModel: string,
): ModelGroup[] {
  // Stable sort: the default jumps to the front, the rest keep the server's
  // (name) order.
  const ordered = [...profiles].sort((a, b) => Number(b.is_default) - Number(a.is_default))
  return ordered.map((profile) => {
    const fromEndpoint = listed[profile.id] ?? []
    let models = fromEndpoint.length > 0 ? fromEndpoint : [profile.chat_model]
    if (profile.id === activeProfileId && currentModel !== '' && !models.includes(currentModel)) {
      models = [currentModel, ...models]
    }
    return {
      profileId: profile.id,
      label: profile.is_default ? `${profile.name} · 默认` : profile.name,
      models,
    }
  })
}

/**
 * A dropdown item's id: the model name is only meaningful on the endpoint it
 * was listed from, so the pick carries its profile along. Profile ids are
 * UUIDs and never contain the separator, and the parser splits on the first
 * one, so a model name containing it survives the round trip.
 */
export function modelOptionId(profileId: string, model: string): string {
  return `${profileId}::${model}`
}

/** The pick an item id names, or null when the id is not one of ours. */
export function parseModelOptionId(id: string): { profileId: string; model: string } | null {
  const separator = id.indexOf('::')
  if (separator <= 0 || separator === id.length - 2) {
    return null
  }
  return { profileId: id.slice(0, separator), model: id.slice(separator + 2) }
}

export interface ModelChoice {
  profiles: ModelProfile[]
  /** The configuration that will answer. Null until the list arrives, or when
   *  there is nothing configured, and then there is nothing to offer either. */
  profile: ModelProfile | null
  /** What the dropdown lists: one section per profile, the default first. */
  groups: ModelGroup[]
  /** What goes on the wire as `chat_model`. */
  model: string
  /** The profile the choice pinned; null while the user has never picked. */
  profileId: string | null
  /** Switches to `profileId` and puts `model` on the wire as `chat_model`. */
  setModel(profileId: string, model: string): void
  /** Where the thinking dial is. */
  level: ThinkingLevel
  setLevel(level: ThinkingLevel): void
}

export function useModelChoice(fallbackProfileId: string | null): ModelChoice {
  const stored = useSyncExternalStore(subscribe, getSnapshot)
  const [profiles, setProfiles] = useState<ModelProfile[]>([])
  const [listed, setListed] = useState<Record<string, string[]>>({})

  useEffect(() => {
    let dropped = false
    void listModelProfiles()
      .then((loaded) => {
        if (!dropped) {
          setProfiles(loaded)
        }
      })
      .catch(() => {
        // An unconfigured backend answers 409 here. The composer still works;
        // what is missing is a choice, and there is nothing to choose from.
      })
    return () => {
      dropped = true
    }
  }, [])

  // A pinned profile that is gone (deleted while the app was closed) cannot
  // answer anything; the choice falls back to the conversation's own profile
  // and the pin is cleared so the next pick starts from reality.
  useEffect(() => {
    if (
      stored.profileId !== null &&
      profiles.length > 0 &&
      !profiles.some((item) => item.id === stored.profileId)
    ) {
      setChoice({ profileId: null, chatModel: '' })
    }
  }, [profiles, stored.profileId])

  const profile = useMemo(() => {
    const pinned =
      stored.profileId !== null
        ? profiles.find((item) => item.id === stored.profileId)
        : undefined
    if (pinned !== undefined) {
      return pinned
    }
    return profiles.find((item) => item.id === fallbackProfileId) ?? effectiveDefault(profiles)
  }, [profiles, fallbackProfileId, stored.profileId])

  const activeId = profile?.id ?? null

  useEffect(() => {
    if (profiles.length === 0) {
      return
    }
    let dropped = false
    // Cleared first: a section keeps its previous fetch until its own returns,
    // and the fallback would then offer names the endpoint may have stopped
    // serving. Blank first, fill as the lists arrive.
    setListed({})
    void Promise.all(
      profiles.map(async (item): Promise<[string, string[]]> => {
        try {
          const result = await listProfileModels(item.id)
          return [item.id, modelNames(result.models)]
        } catch {
          // The section falls back to the profile's own model name, which is
          // always a usable list of one.
          return [item.id, []]
        }
      }),
    ).then((entries) => {
      if (!dropped) {
        setListed(Object.fromEntries(entries))
      }
    })
    return () => {
      dropped = true
    }
  }, [profiles])

  const groups = useMemo(
    () => buildModelGroups(profiles, listed, activeId, stored.chatModel),
    [profiles, listed, activeId, stored.chatModel],
  )

  const model = stored.chatModel !== '' ? stored.chatModel : (profile?.chat_model ?? '')

  return {
    profiles,
    profile,
    groups,
    model,
    profileId: stored.profileId,
    // Picking a model pins the profile it was listed under: the name is only
    // meaningful on that endpoint, and the pin is what carries the choice into
    // conversations whose own profile is a different one.
    setModel: (pickedProfileId, pickedModel) =>
      setChoice({ profileId: pickedProfileId, chatModel: pickedModel }),
    // The user's intent, not what it works out to for the profile on screen: it
    // has to survive the moment before the profile list arrives. What a level
    // means on the wire is the backend's business - 关 is the profile's own
    // fragment, and the strengths are a field this app sets itself, so neither
    // of them needs anything from the profile to be worth offering.
    level: stored.level,
    setLevel: (level) => setChoice({ level }),
  }
}
