/** 组件级:AI 生成的 SQL 必须落在生成它的那个连接的页签上,绝不复用其他连接的页签。 */
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'

vi.mock('@/api/http', () => ({
  get: vi.fn(async () => ({ items: [] })),
  post: vi.fn(async () => ({ item: { id: 'srv', title: '新会话', connection_id: null }, ok: true })),
}))
vi.mock('@/api/ws', () => ({ ws: { send: vi.fn(async () => 'r1'), done: vi.fn() } }))

import AiPanel from '@/components/AiPanel.vue'
import { useAiStore } from '@/stores/ai'
import { useWorkspaceStore } from '@/stores/workspace'

/** 会话绑定 c-target;工作区里可能已经有别的连接的 SQL 页签 */
function prepare(sessionConn: string | null) {
  const ai = useAiStore()
  ai.sessions = [{ id: 's1', title: '会话', connection_id: sessionConn }]
  ai.currentSessionId = 's1'
  ai.messages = [{ id: 'm1', role: 'assistant', text: '好', sql: 'SELECT 1' }]
  return ai
}

async function clickInsert() {
  const w = mount(AiPanel)
  await nextTick()
  const btn = w.findAll('button').find(b => b.text() === '插入到编辑器')
  expect(btn).toBeTruthy()
  await btn!.trigger('click')
  return w
}

beforeEach(() => setActivePinia(createPinia()))

describe('AiPanel · SQL 落点', () => {
  it('工作区只有其他连接的 SQL 页签时:在生成 SQL 的连接上新建页签', async () => {
    prepare('c-target')
    const workspace = useWorkspaceStore()
    const other = workspace.addTab({ type: 'sql', connection_id: 'c-other', content: 'SELECT 9' })

    await clickInsert()

    const target = workspace.tabs.find(t => t.connection_id === 'c-target')
    expect(target).toBeTruthy()                       // 新建在正确的连接上
    expect(target!.content).toContain('SELECT 1')
    expect(other.content).toBe('SELECT 9')            // 其他连接的页签没被写入
    expect(workspace.activeId).toBe(target!.id)       // "插入并执行"要能落到该页签
  })

  it('已有同连接的 SQL 页签时:复用该页签,不新建', async () => {
    prepare('c-target')
    const workspace = useWorkspaceStore()
    const mine = workspace.addTab({ type: 'sql', connection_id: 'c-target', content: 'SELECT 0' })

    await clickInsert()

    expect(workspace.tabs).toHaveLength(1)
    expect(mine.content).toContain('SELECT 0')
    expect(mine.content).toContain('SELECT 1')
  })

  it('会话未绑定连接时:不借用其他连接的页签,落到无连接的 SQL 页签', async () => {
    prepare(null)
    const workspace = useWorkspaceStore()
    const other = workspace.addTab({ type: 'sql', connection_id: 'c-other', content: 'SELECT 9' })

    await clickInsert()

    const target = workspace.tabs.find(t => t.id !== other.id)
    expect(target?.connection_id ?? null).toBeNull()
    expect(other.content).toBe('SELECT 9')
  })
})
