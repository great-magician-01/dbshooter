/** workspace store:页签生命周期 + 防抖持久化(http 全部 mock)。 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/http', () => ({
  get: vi.fn(async () => ({ items: [] })),
  post: vi.fn(async () => ({ item: {}, ok: true })),
}))

import { get, post } from '@/api/http'
import { useWorkspaceStore } from '@/stores/workspace'

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe('workspace store', () => {
  it('addTab:新增即激活,并立即持久化', () => {
    const ws = useWorkspaceStore()
    const tab = ws.addTab({ type: 'sql', connection_id: 'c1' })
    expect(ws.activeId).toBe(tab.id)
    expect(ws.tabs).toHaveLength(1)
    expect(post).toHaveBeenCalledWith('/api/workspace/tabs/save',
      expect.objectContaining({ id: tab.id, type: 'sql' }))
    expect(post).toHaveBeenCalledWith('/api/workspace/tabs/order',
      expect.objectContaining({ active_id: tab.id }))
  })

  it('closeTab:关闭后激活相邻页签并删除', () => {
    const ws = useWorkspaceStore()
    const t1 = ws.addTab({ type: 'sql' })
    const t2 = ws.addTab({ type: 'sql' })
    ws.closeTab(t2.id)
    expect(ws.tabs.map(t => t.id)).toEqual([t1.id])
    expect(ws.activeId).toBe(t1.id)
    expect(post).toHaveBeenCalledWith('/api/workspace/tabs/delete', { id: t2.id })
  })

  it('setContent:内容更新 + 防抖 800ms 落库', async () => {
    vi.useFakeTimers()
    const ws = useWorkspaceStore()
    const tab = ws.addTab({ type: 'sql' })
    vi.clearAllMocks()
    ws.setContent(tab.id, 'SELECT 1')
    ws.setContent(tab.id, 'SELECT 12')   // 连续输入只保留最后一次
    expect(post).not.toHaveBeenCalled()
    vi.advanceTimersByTime(900)
    expect(post).toHaveBeenCalledTimes(1)
    expect(post).toHaveBeenCalledWith('/api/workspace/tabs/save',
      expect.objectContaining({ content: 'SELECT 12' }))
    vi.useRealTimers()
  })

  it('load:恢复页签并聚焦 is_active', async () => {
    vi.mocked(get).mockResolvedValueOnce({ items: [
      { id: 'a', type: 'sql', title: 'SQL-1', connection_id: null, context: {},
        content: 'SELECT 1', sort: 0, is_active: false },
      { id: 'b', type: 'redis', title: '键浏览', connection_id: null, context: {},
        content: '', sort: 1, is_active: true },
    ] })
    const ws = useWorkspaceStore()
    await ws.load()
    expect(ws.tabs).toHaveLength(2)
    expect(ws.activeId).toBe('b')
  })
})
