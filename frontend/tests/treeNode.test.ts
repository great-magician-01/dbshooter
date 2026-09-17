/** 组件级:树节点加载失败要出声(error 提示节点),连接变更后已缓存的子节点要作废重拉。 */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/http', () => ({
  get: vi.fn(async () => ({ items: [] })),
  post: vi.fn(async () => ({ item: { id: 'c1' } })),
}))

import TreeNode from '@/components/TreeNode.vue'
import { useConnectionsStore } from '@/stores/connections'
import type { Connection, MetaNode } from '@/types'

const conn: Connection = {
  id: 'c1', name: '本地', type: 'mysql', host: '127.0.0.1', port: 3306,
  database: 'demo', username: 'root', has_password: false, params: {}, readonly: false,
}
const node = (label: string, path: string): MetaNode =>
  ({ path, label, kind: 'database', has_children: true, extra: {} })

/** depth=1:节点默认收起,点击一次才会触发懒加载 */
function mountNode() {
  return mount(TreeNode, { props: { conn, node: node('demo', 'demo'), depth: 1 } })
}

beforeEach(() => setActivePinia(createPinia()))

describe('TreeNode · 加载失败与缓存失效', () => {
  it('子节点加载失败:插入不可展开的提示节点,而不是静默空数组', async () => {
    const conns = useConnectionsStore()
    vi.spyOn(conns, 'metadata').mockRejectedValue(new Error('连接已断开'))
    const w = mountNode()
    await w.find('.tn-row').trigger('click')
    await flushPromises()

    expect(w.text()).toContain('加载失败:连接已断开')
    expect(w.findAll('.tn-row.err')).toHaveLength(1)
    expect(w.find('.tn-row.err').classes()).toContain('err')
    // 提示节点不能展开(点了也不发请求)
    await w.find('.tn-row.err').trigger('click')
    expect(conns.metadata).toHaveBeenCalledTimes(1)
  })

  it('保存/删除连接后:已加载的子节点作废并重拉', async () => {
    const conns = useConnectionsStore()
    const spy = vi.spyOn(conns, 'metadata').mockResolvedValue([
      { path: 'demo.t1', label: 't1', kind: 'table', has_children: false, extra: {} },
    ])
    const w = mountNode()
    await w.find('.tn-row').trigger('click')
    await flushPromises()
    expect(w.text()).toContain('t1')
    expect(spy).toHaveBeenCalledTimes(1)

    // 走真实 store 动作驱动 metaVersion(改名 / 换库后树里不能还是旧结构)
    await conns.save({ id: 'c1', name: '本地(改名)', type: 'mysql' })
    await flushPromises()
    expect(spy).toHaveBeenCalledTimes(2)
    expect(w.text()).toContain('t1')

    await conns.remove('c1')
    await flushPromises()
    expect(spy).toHaveBeenCalledTimes(3)
  })
})
