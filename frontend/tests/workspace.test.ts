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

  it('setContent:防抖按页签分桶,A 页签的待发保存不被 B 页签取消', () => {
    vi.useFakeTimers()
    const ws = useWorkspaceStore()
    const a = ws.addTab({ type: 'sql' })
    const b = ws.addTab({ type: 'sql' })
    vi.clearAllMocks()
    ws.setContent(a.id, 'A-1')
    vi.advanceTimersByTime(300)
    ws.setContent(b.id, 'B-1')          // 不应把 A 的定时器清掉
    vi.advanceTimersByTime(500)         // A 的 800ms 到点
    expect(post).toHaveBeenCalledWith('/api/workspace/tabs/save',
      expect.objectContaining({ id: a.id, content: 'A-1' }))
    vi.advanceTimersByTime(300)         // B 的 800ms 到点
    expect(post).toHaveBeenCalledWith('/api/workspace/tabs/save',
      expect.objectContaining({ id: b.id, content: 'B-1' }))
    vi.useRealTimers()
  })

  it('setContent:定时器到时发送的是最新内容(不是入队时的旧闭包)', () => {
    vi.useFakeTimers()
    const ws = useWorkspaceStore()
    const tab = ws.addTab({ type: 'sql' })
    vi.clearAllMocks()
    ws.setContent(tab.id, 'SELECT 1')
    ws.setContent(tab.id, 'SELECT 2')
    vi.advanceTimersByTime(900)
    expect(post).toHaveBeenCalledTimes(1)
    expect(post).toHaveBeenCalledWith('/api/workspace/tabs/save',
      expect.objectContaining({ content: 'SELECT 2' }))
    vi.useRealTimers()
  })

  it('closeTab:取消待发的防抖保存,已删页签不会被复活', () => {
    vi.useFakeTimers()
    const ws = useWorkspaceStore()
    const tab = ws.addTab({ type: 'sql' })
    vi.clearAllMocks()
    ws.setContent(tab.id, 'SELECT 1')
    ws.closeTab(tab.id)
    vi.advanceTimersByTime(2000)
    expect(post).toHaveBeenCalledWith('/api/workspace/tabs/delete', { id: tab.id })
    expect(post).not.toHaveBeenCalledWith('/api/workspace/tabs/save', expect.anything())
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
