/** 工作区页签:状态 + 持久化(内容防抖 800ms,开/关/切换立即写)。 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { get, post } from '@/api/http'
import type { Tab, TabType } from '@/types'

let seq = 1
let saveTimer: ReturnType<typeof setTimeout> | null = null

export const useWorkspaceStore = defineStore('workspace', () => {
  const tabs = ref<Tab[]>([])
  const activeId = ref<string | null>(null)
  /** 最近一次执行的统计,供状态栏展示 */
  const lastRun = ref<{ rows: number; ms: number } | null>(null)

  const activeTab = computed(() => tabs.value.find(t => t.id === activeId.value) ?? null)

  async function load() {
    const { items } = await get<{ items: Tab[] }>('/api/workspace/tabs')
    tabs.value = items
    const active = items.find(t => t.is_active)
    activeId.value = active?.id ?? items[0]?.id ?? null
    seq = items.length + 1
  }

  function persistOrder() {
    post('/api/workspace/tabs/order',
      { ids: tabs.value.map(t => t.id), active_id: activeId.value }).catch(() => {})
  }

  function persistTab(tab: Tab, debounce = false) {
    const send = () => post('/api/workspace/tabs/save', {
      id: tab.id, type: tab.type, title: tab.title, connection_id: tab.connection_id,
      context: tab.context, content: tab.content, sort: tab.sort,
    }).catch(() => {})
    if (saveTimer) clearTimeout(saveTimer)
    if (debounce) saveTimer = setTimeout(send, 800)
    else send()
  }

  function addTab(partial: { type: TabType; title?: string; connection_id?: string | null;
                             context?: Record<string, any>; content?: string }): Tab {
    const tab: Tab = {
      id: `t${Date.now()}-${seq++}`,
      type: partial.type,
      title: partial.title ?? `SQL-${seq - 1}`,
      connection_id: partial.connection_id ?? null,
      context: partial.context ?? {},
      content: partial.content ?? '',
      sort: tabs.value.length,
    }
    tabs.value.push(tab)
    activeId.value = tab.id
    persistTab(tab)
    persistOrder()
    return tab
  }

  function closeTab(id: string) {
    const i = tabs.value.findIndex(t => t.id === id)
    if (i < 0) return
    tabs.value.splice(i, 1)
    post('/api/workspace/tabs/delete', { id }).catch(() => {})
    if (activeId.value === id)
      activeId.value = tabs.value[Math.min(i, tabs.value.length - 1)]?.id ?? null
    persistOrder()
  }

  function activate(id: string) {
    if (activeId.value === id) return
    activeId.value = id
    persistOrder()
  }

  /** 编辑器内容变化:更新内存 + 防抖落库 */
  function setContent(id: string, content: string) {
    const tab = tabs.value.find(t => t.id === id)
    if (!tab) return
    tab.content = content
    persistTab(tab, true)
  }

  return { tabs, activeId, activeTab, lastRun, load, addTab, closeTab, activate, setContent }
})
