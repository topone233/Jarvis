/**
 * The model and the thinking dial the composer is offering.
 *
 * One hook owns the profile list, because two things have to agree about it:
 * which configuration will answer, and which model names the dropdown can
 * offer. The rule for the first is the backend's own, mirrored by
 * `effectiveDefault` - the conversation's profile, and failing that the
 * default one - which is also the rule `ChatPage` used to work out the context
 * ring on its own.
 *
 * **Nothing here is remembered.** Not in the database, not in localStorage: a
 * reload comes back to the profile's own model with the dial at 关. That is a
 * decision, not an oversight, so do not "fix" it by persisting the choice.
 */

import { useEffect, useMemo, useState } from 'react'

import { listModelProfiles, listProfileModels } from '../api/endpoints'
import { effectiveDefault, modelNames } from '../api/profiles'
import type { ThinkingLevel } from '../api/thinking'
import type { ModelProfile } from '../api/types'

/** A choice made on a screen whose conversation did not exist yet. */
export interface ModelChoiceSeed {
  chatModel?: string
  level?: ThinkingLevel
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
  setModel(name: string): void
  /** Where the thinking dial is. */
  level: ThinkingLevel
  setLevel(level: ThinkingLevel): void
}

export function useModelChoice(profileId: string | null, seed?: ModelChoiceSeed): ModelChoice {
  const [profiles, setProfiles] = useState<ModelProfile[]>([])
  const [listed, setListed] = useState<string[]>([])
  // Only a choice the user made lives here. Empty means "whatever the profile
  // says", which is why nothing has to be synced when the list arrives a moment
  // after the first render. The seed is read once, at mount: it is the answer to
  // a question that was already asked on the previous screen.
  const [chosen, setChosen] = useState(seed?.chatModel ?? '')
  const [level, setLevel] = useState(seed?.level ?? 'off')

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

  const profile = useMemo(
    () => profiles.find((item) => item.id === profileId) ?? effectiveDefault(profiles),
    [profiles, profileId],
  )
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
    const current = chosen !== '' ? chosen : (profile?.chat_model ?? '')
    // A profile whose `chat_model` is not in the endpoint's own list is a real
    // state - nothing keeps the two boxes in step - and the dropdown has to be
    // able to show what is in use.
    return current !== '' && !names.includes(current) ? [current, ...names] : names
  }, [listed, profile, chosen])

  const model = chosen !== '' ? chosen : (profile?.chat_model ?? '')

  return {
    profiles,
    profile,
    models,
    model,
    setModel: setChosen,
    // The user's intent, not what it works out to for the profile on screen: it
    // has to survive the moment before the profile list arrives, because a first
    // message goes out on the render the seed lands on. What a level means on
    // the wire is the backend's business - 关 is the profile's own fragment, and
    // the three strengths are a field this app sets itself, so neither of them
    // needs anything from the profile to be worth offering.
    level,
    setLevel,
  }
}
