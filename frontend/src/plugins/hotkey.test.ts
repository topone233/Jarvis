import { describe, expect, it } from 'vitest'

import { describeEvent, formatHotkey, hotkeyMatches, parseHotkey, syntheticInit } from './hotkey'

/** The parts of KeyboardEvent the matchers read; tests run in node. */
function keyEvent(parts: Partial<KeyboardEvent>): KeyboardEvent {
  return {
    ctrlKey: false,
    altKey: false,
    shiftKey: false,
    metaKey: false,
    key: '',
    code: '',
    isComposing: false,
    ...parts,
  } as KeyboardEvent
}

describe('parsing the stored string', () => {
  it('reads a combo with modifiers in any case and order', () => {
    expect(parseHotkey('alt+n')).toEqual({
      ctrl: false,
      alt: true,
      shift: false,
      meta: false,
      key: 'N',
    })
    expect(parseHotkey('Ctrl+Shift+N')).toEqual({
      ctrl: true,
      alt: false,
      shift: true,
      meta: false,
      key: 'N',
    })
  })

  it('reads function keys', () => {
    expect(parseHotkey('f9')?.key).toBe('F9')
    expect(parseHotkey('Ctrl+F12')?.key).toBe('F12')
  })

  it('refuses a bare key, a bare modifier, two keys, and junk', () => {
    expect(parseHotkey('N')).toBeNull()
    expect(parseHotkey('Alt')).toBeNull()
    expect(parseHotkey('Alt+N+M')).toBeNull()
    expect(parseHotkey('Alt+Enter')).toBeNull()
    expect(parseHotkey('')).toBeNull()
  })

  it('accepts win as an alias of the meta modifier, because that is what formatHotkey prints', () => {
    expect(parseHotkey('Win+M')).toEqual({
      ctrl: false,
      alt: false,
      shift: false,
      meta: true,
      key: 'M',
    })
    expect(parseHotkey('Win+9')).toEqual({
      ctrl: false,
      alt: false,
      shift: false,
      meta: true,
      key: '9',
    })
  })
})

describe('displaying a combo', () => {
  it('puts modifiers in the fixed order with the key upper-cased', () => {
    expect(formatHotkey({ ctrl: true, alt: true, shift: true, meta: false, key: 'N' })).toBe(
      'Ctrl+Alt+Shift+N',
    )
    expect(formatHotkey({ ctrl: false, alt: false, shift: false, meta: true, key: '9' })).toBe(
      'Win+9',
    )
  })
})

describe('matching keyboard events', () => {
  const altN = parseHotkey('Alt+N')!

  it('matches the same chord, reading letters off event.code', () => {
    expect(hotkeyMatches(altN, keyEvent({ altKey: true, key: 'n', code: 'KeyN' }))).toBe(true)
    // Shift+N would be a different chord even though key is the letter.
    expect(
      hotkeyMatches(altN, keyEvent({ altKey: true, shiftKey: true, key: 'N', code: 'KeyN' })),
    ).toBe(false)
    expect(hotkeyMatches(altN, keyEvent({ ctrlKey: true, key: 'n', code: 'KeyN' }))).toBe(false)
  })

  it('matches a function key by name', () => {
    const f5 = parseHotkey('F5')!
    expect(hotkeyMatches(f5, keyEvent({ key: 'F5', code: 'F5' }))).toBe(true)
    expect(hotkeyMatches(f5, keyEvent({ key: 'F6', code: 'F6' }))).toBe(false)
  })
})

describe('synthesizing a keydown for the desktop shell', () => {
  it('round-trips: the synthetic event matches its combo like a real keypress would', () => {
    for (const text of ['Alt+N', 'Ctrl+Shift+5', 'F5', 'Ctrl+F12', 'Win+M']) {
      const combo = parseHotkey(text)!
      expect(hotkeyMatches(combo, keyEvent(syntheticInit(combo) as Partial<KeyboardEvent>))).toBe(
        true,
      )
    }
  })

  it('letters ride on event.code, the stable leg under IME and Shift', () => {
    expect(syntheticInit(parseHotkey('Alt+N')!).code).toBe('KeyN')
    expect(syntheticInit(parseHotkey('F5')!).key).toBe('F5')
    expect(syntheticInit(parseHotkey('Ctrl+Shift+5')!).code).toBe('Digit5')
  })

  it('a different chord does not match the synthetic event', () => {
    const altN = parseHotkey('Alt+N')!
    const shifted = syntheticInit(altN)
    expect(
      hotkeyMatches(altN, keyEvent({ ...(shifted as Partial<KeyboardEvent>), shiftKey: true })),
    ).toBe(false)
  })
})

describe('recording a pressed key', () => {
  it('reads a full chord into the display string', () => {
    expect(describeEvent(keyEvent({ altKey: true, key: 'n', code: 'KeyN' }))).toBe('Alt+N')
    expect(describeEvent(keyEvent({ ctrlKey: true, shiftKey: true, key: 'k', code: 'KeyK' }))).toBe(
      'Ctrl+Shift+K',
    )
  })

  it('ignores lone modifiers, Escape, and modifier-less keys', () => {
    expect(describeEvent(keyEvent({ key: 'Control', code: 'ControlLeft' }))).toBeNull()
    expect(describeEvent(keyEvent({ key: 'Escape', code: 'Escape' }))).toBeNull()
    expect(describeEvent(keyEvent({ key: 'n', code: 'KeyN' }))).toBeNull()
  })
})
