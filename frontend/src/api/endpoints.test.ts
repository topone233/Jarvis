import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))

vi.mock('./client', () => ({ api: apiMock }))

import { listConversations } from './endpoints'

describe('the conversation list request', () => {
  beforeEach(() => {
    apiMock.mockReset()
  })

  it('asks for everything with no options', async () => {
    await listConversations()
    expect(apiMock).toHaveBeenCalledWith('/api/conversations')
  })

  it('carries the search keyword as q', async () => {
    await listConversations({ query: '压缩' })
    expect(apiMock).toHaveBeenCalledWith('/api/conversations?q=%E5%8E%8B%E7%BC%A9')
  })

  it('sends no q for an empty keyword, which is "no filter"', async () => {
    await listConversations({ query: '' })
    expect(apiMock).toHaveBeenCalledWith('/api/conversations')
  })

  it('combines a project filter with a search', async () => {
    await listConversations({ projectId: 'p1', query: 'a b' })
    expect(apiMock).toHaveBeenCalledWith('/api/conversations?project_id=p1&q=a+b')
  })
})
