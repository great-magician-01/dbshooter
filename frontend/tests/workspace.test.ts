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

/**
 * 按 URL 过滤的调用:persistOrder 有 300ms 防抖,推进时间会把它的请求一并带出来,
 * 因此校验单个接口时不能直接用 "总调用次数"。
 */
function callsTo(url: string) {
  return vi.mocked(post).mock.calls.filter(c => c[0] === url)
}

describe('workspace store', () => {
  it('addTab:新增即激活,内容立即落库、顺序 300ms 防抖后落库', () => {
    vi.useFakeTimers()
    const ws = useWorkspaceStore()
    const tab = ws.addTab({ type: 'sql', connection_id: 'c1' })
    expect(ws.activeId).toBe(tab.id)
    expect(ws.tabs).toHaveLength(1)
    expect(post).toHaveBeenCalledWith('/api/workspace/tabs/save',
      expect.objectContaining({ id: tab.id, type: 'sql' }))
    expect(callsTo('/api/workspace/tabs/order')).toHaveLength(0)
    vi.advanceTimersByTime(400)
    expect(post).toHaveBeenCalledWith('/api/workspace/tabs/order',
      expect.objectContaining({ active_id: tab.id }))
    vi.useRealTimers()
  })

  it('addTab:表详情(table)页签同样立即落库,type 原样透传', () => {
    const ws = useWorkspaceStore()
    const tab = ws.addTab({ type: 'table', connection_id: 'c1', title: 'orders',
                            context: { table: 'orders', path: 'demo.orders' } })
    expect(post).toHaveBeenCalledWith('/api/workspace/tabs/save',
      expect.objectContaining({ id: tab.id, type: 'table',
                                context: expect.objectContaining({ path: 'demo.orders' }) }))
  })

  it('persistOrder:连续操作合并为一次写入', () => {
    vi.useFakeTimers()
    const ws = useWorkspaceStore()
    const t1 = ws.addTab({ type: 'sql' })
    const t2 = ws.addTab({ type: 'sql' })
    ws.activate(t1.id)
    vi.advanceTimersByTime(400)
    expect(callsTo('/api/workspace/tabs/order')).toHaveLength(1)
    expect(post).toHaveBeenCalledWith('/api/workspace/tabs/order',
      expect.objectContaining({ ids: [t1.id, t2.id], active_id: t1.id }))
    vi.useRealTimers()
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
    expect(callsTo('/api/workspace/tabs/save')).toHaveLength(0)
    vi.advanceTimersByTime(900)
    expect(callsTo('/api/workspace/tabs/save')).toHaveLength(1)
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
    expect(callsTo('/api/workspace/tabs/save')).toHaveLength(1)
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

  it('load:合并本地已建但服务端还没有的页签,并补一次持久化', async () => {
    const ws = useWorkspaceStore()
    const local = ws.addTab({ type: 'sql', connection_id: 'c9' })   // load 在途时新建的页签
    vi.clearAllMocks()
    vi.mocked(get).mockResolvedValueOnce({ items: [
      { id: 'a', type: 'sql', title: 'SQL-1', connection_id: null, context: {},
        content: '', sort: 0, is_active: true },
    ] })
    await ws.load()
    // 服务端页签 + 本地页签(直接赋值会让本地页签凭空消失)
    expect(ws.tabs.map(t => t.id)).toEqual(['a', local.id])
    expect(ws.activeId).toBe(local.id)            // 用户当前停留的本地页签不被抢走
    expect(post).toHaveBeenCalledWith('/api/workspace/tabs/save',
      expect.objectContaining({ id: local.id }))
  })
})
