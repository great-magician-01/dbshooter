/** 连接管理 + 元数据懒加载缓存。 */
import { defineStore } from 'pinia'
import { ref } from 'vue'

import { get, post } from '@/api/http'
import type { Connection, ConnectionSave, MetaNode } from '@/types'

export const useConnectionsStore = defineStore('connections', () => {
  const items = ref<Connection[]>([])
  /** 元数据代次:连接增删改后 +1,TreeNode 据此丢弃已加载的子节点(否则树里还是旧结构) */
  const metaVersion = ref(0)
  /** `${connId}|${path}` → 已加载的子节点 */
  const metaCache = ref<Map<string, MetaNode[]>>(new Map())
  /** `${connId}|${path}` → 在途请求(同一 path 未返回时不重复发) */
  const metaPending = new Map<string, Promise<MetaNode[]>>()

  async function load() {
    items.value = (await get<{ items: Connection[] }>('/api/connections')).items
  }

  async function save(cfg: ConnectionSave): Promise<Connection> {
    const url = cfg.id ? '/api/connections/update' : '/api/connections'
    const { item } = await post<{ item: Connection }>(url, cfg)
    // 配置变了,该连接的全部元数据缓存作废
    for (const key of [...metaCache.value.keys()])
      if (key.startsWith(`${item.id}|`)) metaCache.value.delete(key)
    metaVersion.value++
    await load()
    return item
  }

  async function remove(id: string) {
    await post('/api/connections/delete', { id })
    for (const key of [...metaCache.value.keys()])
      if (key.startsWith(`${id}|`)) metaCache.value.delete(key)
    metaVersion.value++
    await load()
  }

  async function test(payload: { id?: string; config?: ConnectionSave }) {
    return post<{ ok: boolean; message: string }>('/api/connections/test', payload)
  }

  /** 元数据懒加载:同一 (connId, path) 只请求一次(含并发去重) */
  function metadata(connId: string, path: string): Promise<MetaNode[]> {
    const key = `${connId}|${path}`
    const hit = metaCache.value.get(key)
    if (hit) return Promise.resolve(hit)
    const inflight = metaPending.get(key)
    if (inflight) return inflight
    const p = get<{ items: MetaNode[] }>(`/api/connections/${connId}/metadata`, { path })
      .then(({ items: nodes }) => { metaCache.value.set(key, nodes); return nodes })
      .finally(() => { metaPending.delete(key) })
    metaPending.set(key, p)
    return p
  }

  return { items, metaVersion, load, save, remove, test, metadata }
})
