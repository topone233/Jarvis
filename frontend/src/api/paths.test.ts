import { describe, expect, it } from 'vitest'

import { sameDirectory } from './paths'

describe('sameDirectory', () => {
  // In a .ts file this *is* a JavaScript string, so `\\` is one backslash.
  it('treats case as the same directory, because Windows does', () => {
    expect(sameDirectory('C:\\Jarvis', 'c:\\jarvis')).toBe(true)
  })

  it('ignores a trailing separator', () => {
    expect(sameDirectory('C:\\Jarvis\\', 'C:\\Jarvis')).toBe(true)
    expect(sameDirectory('C:\\Jarvis/', 'C:\\Jarvis')).toBe(true)
  })

  // Windows takes either separator, and a user typing the path by hand will use
  // whichever they are used to. Reading that as a change would light up the
  // 切换目录 button and reload the page over nothing.
  it('ignores which separator was used', () => {
    expect(sameDirectory('C:/Jarvis', 'C:\\Jarvis')).toBe(true)
    expect(sameDirectory('C:/Jarvis/', 'c:\\Jarvis')).toBe(true)
  })

  // The comparison has to be able to say no, or the button that offers to
  // switch would never appear at all.
  it('keeps two different directories apart', () => {
    expect(sameDirectory('C:\\Jarvis', 'C:\\JarvisData')).toBe(false)
  })

  it('does not treat a prefix as the same directory', () => {
    expect(sameDirectory('C:\\Jarvis', 'C:\\Jarvis\\backup')).toBe(false)
  })
})
