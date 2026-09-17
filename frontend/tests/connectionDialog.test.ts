/** 组件级:连接弹窗的字段随数据库类型切换。 */
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/http', () => ({
  get: vi.fn(async () => ({ items: [] })),
  post: vi.fn(async () => ({ ok: true, message: 'ok', item: { id: 'x' } })),
}))

import { post } from '@/api/http'
import ConnectionDialog from '@/components/dialogs/ConnectionDialog.vue'
import { useConnectionsStore } from '@/stores/connections'
import { useUiStore } from '@/stores/ui'

describe('ConnectionDialog', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('默认 MySQL:显示主机/端口/用户名,隐藏文件路径', () => {
    const w = mount(ConnectionDialog)
    const text = w.text()
    expect(text).toContain('主机')
    expect(text).toContain('端口')
    expect(text).toContain('用户名')
    expect(text).not.toContain('数据库文件路径')
    expect(w.findAll('.db-card')).toHaveLength(5)
  })

  it('切到 SQLite:只显示 连接名/文件路径/只读', async () => {
    const w = mount(ConnectionDialog)
    const cards = w.findAll('.db-card')
    await cards[2].trigger('click')   // sqlite
    const text = w.text()
    expect(text).toContain('数据库文件路径')
    expect(text).toContain('只读模式')
    expect(text).not.toContain('主机')
    expect(text).not.toContain('端口')
  })

  it('切到 Redis:显示 DB 索引,不显示用户名', async () => {
    const w = mount(ConnectionDialog)
    await w.findAll('.db-card')[3].trigger('click')   // redis
    const text = w.text()
    expect(text).toContain('DB 索引')
    expect(text).not.toContain('用户名')
  })

  it('编辑已有连接:测试连接同传 {id, config},让后端回填已存密码', async () => {
    const ui = useUiStore()
    const conns = useConnectionsStore()
    conns.items = [{ id: 'c1', name: '本地', type: 'mysql', host: '127.0.0.1', port: 3306,
      database: 'demo', username: 'root', has_password: true, params: {}, readonly: false }]
    ui.editingConnection = 'c1'
    const w = mount(ConnectionDialog)
    await w.findAll('.modal-foot .btn')[0].trigger('click')   // 测试连接
    expect(post).toHaveBeenCalledWith('/api/connections/test',
      expect.objectContaining({ id: 'c1', config: expect.objectContaining({ id: 'c1' }) }))
  })
})
