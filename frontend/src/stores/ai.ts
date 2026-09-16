/** AI 助手:Provider(多配置单生效)+ 会话/消息持久化 + WS 流式生成。 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { get, post } from '@/api/http'
import { ws } from '@/api/ws'
import type { AiMessage, AiProvider, AiProviderSave, AiSession, AiToolTrace } from '@/types'

function parseMessage(row: any): AiMessage {
  if (row.role === 'assistant') {
    try {
      const c = JSON.parse(row.content)
      return { id: row.id, role: 'assistant', text: c.text ?? '', sql: c.sql ?? null,
               tools: c.tools ?? undefined, elapsed_ms: row.elapsed_ms }
    } catch { /* 历史脏数据按纯文本展示 */ }
  }
  return { id: row.id, role: row.role, text: row.content, sql: null }
}

export const useAiStore = defineStore('ai', () => {
  const providers = ref<AiProvider[]>([])
  const sessions = ref<AiSession[]>([])
  const currentSessionId = ref<string | null>(null)
  const messages = ref<AiMessage[]>([])
  const generating = ref(false)

  const activeProvider = computed(() => providers.value.find(p => p.is_active) ?? null)

  async function loadProviders() {
    providers.value = (await get<{ items: AiProvider[] }>('/api/ai/providers')).items
  }

  async function saveProvider(p: AiProviderSave) {
    await post(p.id ? '/api/ai/providers/update' : '/api/ai/providers', p)
    await loadProviders()
  }

  async function deleteProvider(id: string) {
    await post('/api/ai/providers/delete', { id })
    await loadProviders()
  }

  async function activateProvider(id: string) {
    await post('/api/ai/providers/activate', { id })
    providers.value.forEach(p => { p.is_active = p.id === id })
  }

  async function testProvider(p: AiProviderSave) {
    return post<{ ok: boolean; message: string }>('/api/ai/providers/test', p)
  }

  async function loadSessions() {
    sessions.value = (await get<{ items: AiSession[] }>('/api/ai/sessions')).items
    if (!currentSessionId.value) {
      if (sessions.value[0]) await selectSession(sessions.value[0].id)
      else await newSession()
    }
  }

  async function newSession(connId?: string | null) {
    const { item } = await post<{ item: AiSession }>('/api/ai/sessions',
      { title: '新会话', connection_id: connId ?? null })
    sessions.value.unshift(item)
    currentSessionId.value = item.id
    messages.value = []
    return item
  }

  async function selectSession(id: string) {
    currentSessionId.value = id
    const { items } = await get<{ items: any[] }>(`/api/ai/sessions/${id}/messages`)
    messages.value = items.map(parseMessage)
  }

  async function deleteSession(id: string) {
    await post('/api/ai/sessions/delete', { id })
    sessions.value = sessions.value.filter(s => s.id !== id)
    if (currentSessionId.value === id) {
      currentSessionId.value = null
      messages.value = []
      if (sessions.value[0]) await selectSession(sessions.value[0].id)
      else await newSession()
    }
  }

  /** text2sql:WS 流式;connId 用于取 schema 上下文(缺省回退到会话绑定的连接) */
  async function ask(question: string, connId: string | null) {
    if (!currentSessionId.value) await newSession(connId)
    const sid = currentSessionId.value!
    const effConnId = connId ?? sessions.value.find(s => s.id === sid)?.connection_id ?? null
    if (messages.value.length === 0) {
      // 首条问题自动生成会话标题
      const title = question.slice(0, 24)
      post('/api/ai/sessions/rename', { id: sid, title }).catch(() => {})
      const s = sessions.value.find(s => s.id === sid)
      if (s) s.title = title
    }
    messages.value.push({ role: 'user', text: question, sql: null })
    const assistant: AiMessage = { role: 'assistant', text: '', sql: null, streaming: true }
    messages.value.push(assistant)
    generating.value = true

    await ws.send('ai.text2sql',
      { session_id: sid, conn_id: effConnId, question },
      (ev) => {
        if (ev.event === 'ai.token') assistant.text += ev.data.delta
        else if (ev.event === 'ai.tool') {
          // 工具轨迹按 call_id 合并(running 占位 → done/error 更新)
          assistant.tools ??= []
          const d = ev.data as AiToolTrace
          const idx = assistant.tools.findIndex(t => t.call_id && t.call_id === d.call_id)
          if (idx >= 0) assistant.tools[idx] = { ...assistant.tools[idx], ...d }
          else assistant.tools.push(d)
        } else if (ev.event === 'ai.done') {
          assistant.sql = ev.data.sql
          if (ev.data.tools) assistant.tools = ev.data.tools   // 以服务端轨迹为准
          assistant.streaming = false
          assistant.elapsed_ms = ev.data.elapsed_ms
          generating.value = false
          ws.done(ev.id)
        } else if (ev.event === 'ai.error') {
          assistant.text = ev.data.message
          assistant.streaming = false
          generating.value = false
          ws.done(ev.id)
        }
      })
  }

  return { providers, activeProvider, sessions, currentSessionId, messages,
           generating, loadProviders, saveProvider, deleteProvider, activateProvider,
           testProvider, loadSessions, newSession, selectSession, deleteSession, ask }
})
