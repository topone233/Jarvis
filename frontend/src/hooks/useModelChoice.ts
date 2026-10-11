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
 */

import { useEffect, useMemo, useState, useSyncExternalStore } from 'react'

import { listModelProfiles, listProfileModels } from '../api/endpoints'
import { effectiveDefault, modelNames } from '../api/profiles'
import { isThinkingLevel, type ThinkingLevel } from '../api/thinking'
import type { ModelProfile } from '../api/types'

/** What a choice consists of: which profile answers, which of its models, and where the dial is. */
interface StoredChoice {
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

export interface ModelChoice {
  profiles: ModelProfile[]
  /** The configuration that will answer. Null until the list arrives, or when
   *  there is nothing configured, and then there is nothing to offer either. */
  profile: ModelProfile | null
  /** What the dropdown lists, the current model included. */
  models: string[]
  /** What goes on the wire as `chat_model`. */
  model: string
  /** The profile the choice pinned; null while the user has never picked. */
  profileId: string | null
  setModel(name: string): void
  /** Where the thinking dial is. */
  level: ThinkingLevel
  setLevel(level: ThinkingLevel): void
}

export function useModelChoice(fallbackProfileId: string | null): ModelChoice {
  const stored = useSyncExternalStore(subscribe, getSnapshot)
  const [profiles, setProfiles] = useState<ModelProfile[]>([])
  const [listed, setListed] = useState<string[]>([])

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
    if (activeId === null) {
      return
    }
    let dropped = false
    // Cleared first: the list belongs to one endpoint, and showing the previous
    // one's models while the next list is in flight would offer names this
    // endpoint has never heard of.
    setListed([])
    void listProfileModels(activeId)
      .then((result) => {
        if (!dropped) {
          setListed(modelNames(result.models))
        }
      })
      .catch(() => {
        // The fallback below is the profile's own model name, which is always a
        // usable list of one.
      })
    return () => {
      dropped = true
    }
  }, [activeId])

  const models = useMemo(() => {
    const names = listed.length > 0 ? listed : profile === null ? [] : [profile.chat_model]
    const currentModel = stored.chatModel !== '' ? stored.chatModel : (profile?.chat_model ?? '')
    // A profile whose `chat_model` is not in the endpoint's own list is a real
    // state - nothing keeps the two boxes in step - and the dropdown has to be
    // able to show what is in use.
    return currentModel !== '' && !names.includes(currentModel) ? [currentModel, ...names] : names
  }, [listed, profile, stored.chatModel])

  const model = stored.chatModel !== '' ? stored.chatModel : (profile?.chat_model ?? '')

  return {
    profiles,
    profile,
    models,
    model,
    profileId: stored.profileId,
    // Picking a model pins its profile with it: the name is only meaningful on
    // the endpoint it was listed from, and the pin is what carries the choice
    // into conversations whose own profile is a different one.
    setModel: (name) => setChoice({ profileId: profile?.id ?? null, chatModel: name }),
    // The user's intent, not what it works out to for the profile on screen: it
    // has to survive the moment before the profile list arrives. What a level
    // means on the wire is the backend's business - 关 is the profile's own
    // fragment, and the strengths are a field this app sets itself, so neither
    // of them needs anything from the profile to be worth offering.
    level: stored.level,
    setLevel: (level) => setChoice({ level }),
  }
}
