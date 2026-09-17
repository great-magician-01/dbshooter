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

  /** 保存 Provider(新建 / 更新),返回后端落库后的 item —— 保存后表单靠它重新定位 */
  async function saveProvider(p: AiProviderSave) {
    const { item } = await post<{ item: AiProvider }>(
      p.id ? '/api/ai/providers/update' : '/api/ai/providers', p)
    await loadProviders()
    return item
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

  /**
   * 在途 AI 请求:reqId 要等 ws.send 返回才就绪(前面还有建连握手),
   * 期间用户可能已点取消 —— 用独立的状态对象记录,握手完成后补走取消路径。
   * msg 存消息的响应式引用(不是下标):期间切换过会话时,下标会指向别的消息。
   */
  interface AskState {
    sessionId: string
    msgIdx: number
    msg: AiMessage
    reqId: string | null
    cancelled: boolean
  }
  let curAsk: AskState | null = null
  /** 最近一次生成用的连接(按会话记录):AI 出的 SQL 必须插回生成它的那个连接 */
  const lastGen = ref<{ sessionId: string; connId: string | null } | null>(null)

  /** 某会话生成 SQL 时用的连接:优先最近一次生成的记录,历史消息回退到会话绑定的连接 */
  function connForSession(sessionId: string | null): string | null {
    if (!sessionId) return null
    if (lastGen.value?.sessionId === sessionId) return lastGen.value.connId
    return sessions.value.find(s => s.id === sessionId)?.connection_id ?? null
  }

  /** text2sql:WS 流式;connId 用于取 schema 上下文(缺省回退到会话绑定的连接) */
  async function ask(question: string, connId: string | null) {
    if (!currentSessionId.value) {
      // 纳入 try:新建会话失败时没有消息可收尾,抛给调用方(AiPanel)提示并恢复输入
      try { await newSession(connId) } catch (e: any) {
        throw new Error(`会话创建失败:${e?.message ?? '未知错误'}`)
      }
    }
    const sid = currentSessionId.value!
    const effConnId = connId ?? sessions.value.find(s => s.id === sid)?.connection_id ?? null
    lastGen.value = { sessionId: sid, connId: effConnId }
    if (messages.value.length === 0) {
      // 首条问题自动生成会话标题
      const title = question.slice(0, 24)
      post('/api/ai/sessions/rename', { id: sid, title }).catch(() => {})
      const s = sessions.value.find(s => s.id === sid)
      if (s) s.title = title
    }
    // 已有在途请求(正常入口不会发生):先按取消收尾,避免旧 handler 留在 WS 注册表里
    if (curAsk) cancelAsk()
    messages.value.push({ role: 'user', text: question, sql: null })
    messages.value.push({ role: 'assistant', text: '', sql: null, streaming: true })
    // 注意:push 进去的是 raw 对象,之后必须经 messages.value[aIdx] 代理读写,
    // 否则流式增量不触发响应式(打字机失效)
    const aIdx = messages.value.length - 1
    generating.value = true
    let finished = false

    /** 本次请求的状态:事件回调与取消路径都凭它判断"还是不是我" */
    const my: AskState = { sessionId: sid, msgIdx: aIdx, msg: messages.value[aIdx],
                           reqId: null, cancelled: false }
    curAsk = my

    /** 终态收尾:无论消息是否还在当前会话,都要复位状态并释放 WS handler */
    const finish = (rid: string) => {
      finished = true
      if (curAsk === my) curAsk = null
      generating.value = false
      ws.done(rid)
    }

    try {
      const rid = await ws.send('ai.text2sql',
        { session_id: sid, conn_id: effConnId, question },
        (ev) => {
          // 已取消 / 已被新请求顶替:迟到事件一律丢弃(否则旧 ai.done 会把新请求的 generating 置假)
          if (my.cancelled || curAsk !== my) return
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
      if (my.cancelled) {
        // 建连握手期间用户就点了取消:此时才拿到 reqId,立刻补走取消路径摘掉 handler,
        // 否则它会留在注册表里 —— 下一次 ask 之后,旧请求的终态事件仍会被正常处理
        ws.done(rid)
      } else if (!finished) {
        // 事件流可能在本行之前就已终结(finish 已置空 reqId),别把已结束的请求写回在途状态
        my.reqId = rid
      }
    } catch (e: any) {
      if (my.cancelled) return      // 已取消:连接失败也不必再写"请求失败"
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

  /** 本地取消当前生成:摘掉 WS handler + 复位状态(服务端任务的中断由后端自行处理) */
  function cancelAsk() {
    const my = curAsk
    if (!my) return
    my.cancelled = true
    if (my.reqId) { ws.done(my.reqId); my.reqId = null }
    // reqId 未就绪(还在建连握手)时不做别的:send 返回后会立刻补走取消路径
    curAsk = null
    if (!generating.value) return
    generating.value = false
    // 只改"还是同一条消息"的那条:期间切换过会话的话,同一下标已是别的会话的消息
    const m = messages.value[my.msgIdx] as AiMessage | undefined
    if (m && m === my.msg && currentSessionId.value === my.sessionId) {
      m.streaming = false
      if (!m.text) m.text = '(已取消)'
    }
  }

  return { providers, activeProvider, sessions, currentSessionId, messages,
           generating, loadProviders, saveProvider, deleteProvider, activateProvider,
           testProvider, loadSessions, newSession, selectSession, deleteSession, ask, cancelAsk,
           connForSession }
})
