import { describe, expect, it } from 'vitest'

import { COMMAND_VISIBLE_LINES, commandFold, commandText } from './commandFold'

describe('commandFold', () => {
  it('leaves a command that fits the visible lines unfolded', () => {
    const command = Array.from({ length: COMMAND_VISIBLE_LINES }, (_, i) => `echo ${i}`).join('\n')
    expect(commandFold(command)).toEqual({ hidden: 0, label: '展开全部' })
  })

  it('counts the whole lines a longer command hides', () => {
    expect(commandFold('a\nb\nc\nd\ne')).toEqual({ hidden: 2, label: '+2 行' })
  })

  it('does not count the newline a script usually ends with', () => {
    expect(commandFold('a\nb\nc\n').hidden).toBe(0)
    expect(commandFold('a\nb\nc\nd\n').label).toBe('+1 行')
  })

  it('hides no whole line behind a command that is one long line', () => {
    // The clamp still cuts it - it wraps into many lines - but there is no
    // line count to promise, so the bar does not name one.
    expect(commandFold(`echo ${'x'.repeat(600)}`)).toEqual({ hidden: 0, label: '展开全部' })
  })

  it('leaves a one-line command alone', () => {
    expect(commandFold('ls -la')).toEqual({ hidden: 0, label: '展开全部' })
  })
})

describe('commandText', () => {
  it('drops the trailing newline, so the block is not a line taller than the command', () => {
    expect(commandText('echo a\necho b\n')).toBe('echo a\necho b')
  })

  it('leaves newlines inside the command alone', () => {
    expect(commandText('echo a\n\necho b')).toBe('echo a\n\necho b')
  })
})
