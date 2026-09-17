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
    messages.value.push({ role: 'assistant', text: '', sql: null, streaming: true })
    // 注意:push 进去的是 raw 对象,之后必须经 messages.value[aIdx] 代理读写,
    // 否则流式增量不触发响应式(打字机失效)
    const aIdx = messages.value.length - 1
    generating.value = true
    let finished = false

    /** 终态收尾:无论消息是否还在当前会话,都要复位状态并释放 WS handler */
    const finish = (reqId: string) => {
      finished = true
      generating.value = false
      ws.done(reqId)
    }

    try {
      await ws.send('ai.text2sql',
        { session_id: sid, conn_id: effConnId, question },
        (ev) => {
          const m = messages.value[aIdx]
          if (ev.event === 'ai.done') {
            if (m) {
              m.sql = ev.data.sql
              if (ev.data.tools) m.tools = ev.data.tools   // 以服务端轨迹为准
              m.elapsed_ms = ev.data.elapsed_ms
              m.streaming = false
            }
            finish(ev.id)
          } else if (ev.event === 'ai.error' || ev.event === 'error') {
            // error:连接断开等服务端兜底事件,按 ai.error 同样收尾
            if (m) {
              m.text = ev.data.message ?? ev.data.error ?? '生成失败'
              m.streaming = false
            }
            finish(ev.id)
          } else if (!m) {
            return                       // 用户已切换会话,中途事件直接丢弃
          } else if (ev.event === 'ai.token') {
            m.text += ev.data.delta
          } else if (ev.event === 'ai.tool') {
            // 工具轨迹按 call_id 合并(running 占位 → done/error 更新)
            m.tools ??= []
            const d = ev.data as AiToolTrace
            const idx = m.tools.findIndex(t => t.call_id && t.call_id === d.call_id)
            if (idx >= 0) m.tools[idx] = { ...m.tools[idx], ...d }
            else m.tools.push(d)
          }
        })
    } catch (e: any) {
      // 连接建立失败(断线 / token 失配)时不会再有终态事件,这里兜底复位,
      // 否则 generating 永久为 true,输入框与发送按钮永久禁用
      if (!finished) {
        const m = messages.value[aIdx]
        if (m) {
          m.text = `请求失败:${e?.message ?? '未知错误'}`
          m.streaming = false
        }
        finish('')
      }
    }
  }

  return { providers, activeProvider, sessions, currentSessionId, messages,
           generating, loadProviders, saveProvider, deleteProvider, activateProvider,
           testProvider, loadSessions, newSession, selectSession, deleteSession, ask }
})
