/** 连接管理 + 元数据懒加载缓存。 */
import { defineStore } from 'pinia'
import { ref } from 'vue'

import { get, post } from '@/api/http'
import type { Connection, ConnectionSave, MetaNode } from '@/types'

export const useConnectionsStore = defineStore('connections', () => {
  const items = ref<Connection[]>([])
  /** `${connId}|${path}` → 已加载的子节点 */
  const metaCache = ref<Map<string, MetaNode[]>>(new Map())

  async function load() {
    items.value = (await get<{ items: Connection[] }>('/api/connections')).items
  }

  async function save(cfg: ConnectionSave): Promise<Connection> {
    const url = cfg.id ? '/api/connections/update' : '/api/connections'
    const { item } = await post<{ item: Connection }>(url, cfg)
    metaCache.value.delete(`${item.id}|`)   // 配置变了,缓存作废
    for (const key of [...metaCache.value.keys()])
      if (key.startsWith(`${item.id}|`)) metaCache.value.delete(key)
    await load()
    return item
  }

  async function remove(id: string) {
    await post('/api/connections/delete', { id })
    await load()
  }

  async function test(payload: { id?: string; config?: ConnectionSave }) {
    return post<{ ok: boolean; message: string }>('/api/connections/test', payload)
  }

  /** 元数据懒加载:同一 (connId, path) 只请求一次 */
  async function metadata(connId: string, path: string): Promise<MetaNode[]> {
    const key = `${connId}|${path}`
    const hit = metaCache.value.get(key)
    if (hit) return hit
    const { items: nodes } = await get<{ items: MetaNode[] }>(
      `/api/connections/${connId}/metadata`, { path })
    metaCache.value.set(key, nodes)
    return nodes
  }

  function invalidateMeta(connId: string) {
    for (const key of [...metaCache.value.keys()])
      if (key.startsWith(`${connId}|`)) metaCache.value.delete(key)
  }

  return { items, load, save, remove, test, metadata, invalidateMeta }
})
