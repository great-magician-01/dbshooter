/** ai store:Provider 单生效切换 + 会话消息解析 + WS 流式问答(http/ws 全部 mock)。 */
import { createPinia, setActivePinia } from 'pinia'
import { nextTick, watch } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/http', () => ({
  get: vi.fn(async () => ({ items: [] })),
  post: vi.fn(async () => ({ item: { id: 's1', title: '新会话' }, ok: true })),
}))
vi.mock('@/api/ws', () => ({
  ws: {
    send: vi.fn(async (_t: string, _p: any, onEvent: (ev: any) => void) => {
      onEvent({ id: 'rid', event: 'ai.token', data: { delta: '```sql\nSELECT 1;' } })
      onEvent({ id: 'rid', event: 'ai.token', data: { delta: '\n```' } })
      onEvent({ id: 'rid', event: 'ai.done',
                data: { text: '```sql\nSELECT 1;\n```', sql: 'SELECT 1;', elapsed_ms: 8 } })
      return 'rid'
    }),
    done: vi.fn(),
  },
}))

import { get, post } from '@/api/http'
import { ws } from '@/api/ws'
import { useAiStore } from '@/stores/ai'

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

const PROVIDERS = [
  { id: 'p1', name: 'DeepSeek', base_url: 'https://a/v1', model: 'm1',
    has_api_key: true, is_active: true },
  { id: 'p2', name: 'Ollama', base_url: 'http://b/v1', model: 'm2',
    has_api_key: false, is_active: false },
]

describe('ai store · provider', () => {
  it('activateProvider:全表唯一生效,本地状态同步', async () => {
    vi.mocked(get).mockResolvedValueOnce({ items: PROVIDERS.map(p => ({ ...p })) })
    const ai = useAiStore()
    await ai.loadProviders()
    expect(ai.activeProvider?.id).toBe('p1')

    await ai.activateProvider('p2')
    expect(post).toHaveBeenCalledWith('/api/ai/providers/activate', { id: 'p2' })
    expect(ai.providers.find(p => p.id === 'p2')!.is_active).toBe(true)
    expect(ai.providers.find(p => p.id === 'p1')!.is_active).toBe(false)
    expect(ai.activeProvider?.name).toBe('Ollama')
  })
})

describe('ai store · provider 保存', () => {
  it('saveProvider:返回后端 item(保存后表单据此重新定位)', async () => {
    vi.mocked(post).mockResolvedValueOnce({ item: { id: 'p9', name: 'X', model: 'm' } })
    const ai = useAiStore()
    const item = await ai.saveProvider({ name: 'X', base_url: 'https://x/v1', model: 'm' })
    expect(item.id).toBe('p9')
    expect(post).toHaveBeenCalledWith('/api/ai/providers',
      expect.objectContaining({ name: 'X' }))
  })
})

describe('ai store · 会话', () => {
  it('selectSession:assistant 消息 JSON 解析为 {text, sql}', async () => {
    const ai = useAiStore()
    vi.mocked(get).mockResolvedValueOnce({ items: [
      { id: 'm1', role: 'user', content: '查用户' },
      { id: 'm2', role: 'assistant',
        content: JSON.stringify({ text: '好的', sql: 'SELECT * FROM users' }) },
    ] })
    await ai.selectSession('s1')
    expect(ai.messages).toHaveLength(2)
    expect(ai.messages[1].sql).toBe('SELECT * FROM users')
    expect(ai.messages[1].text).toBe('好的')
  })

  it('ask:WS 流式拼接到消息,done 后写入 sql', async () => {
    const ai = useAiStore()
    await ai.ask('查所有用户', null)
    expect(ai.messages[0].role).toBe('user')
    const bot = ai.messages[1]
    expect(bot.text).toContain('SELECT 1;')
    expect(bot.sql).toBe('SELECT 1;')
    expect(bot.streaming).toBe(false)
  })
})

describe('ai store · 流式响应式与失败路径', () => {
  it('ask:每个 delta 都经代理写入,生成期间即可驱动视图(打字机)', async () => {
    const ai = useAiStore()
    const seen: string[] = []
    // sync 刷新的 watcher:raw 对象直改不会触发,只有走响应式代理才会
    const stop = watch(() => ai.messages[1]?.text ?? '', v => seen.push(v), { flush: 'sync' })
    vi.mocked(ws.send).mockImplementationOnce(async (_t, _p, onEvent) => {
      onEvent({ id: 'r', event: 'ai.token', data: { delta: '```sql\n' } })
      await nextTick()
      expect(seen).toContain('```sql\n')
      onEvent({ id: 'r', event: 'ai.token', data: { delta: 'SELECT 1;' } })
      await nextTick()
      expect(seen).toContain('```sql\nSELECT 1;')
      onEvent({ id: 'r', event: 'ai.done', data: { text: '', sql: 'SELECT 1;', elapsed_ms: 3 } })
      return 'r'
    })
    await ai.ask('查一下', null)
    stop()
    expect(ai.messages[1].text).toBe('```sql\nSELECT 1;')
    expect(ai.messages[1].sql).toBe('SELECT 1;')
    expect(ai.messages[1].streaming).toBe(false)
  })

  it('ask:发送失败(断线)时复位 generating 并提示,不会永久卡死', async () => {
    const ai = useAiStore()
    vi.mocked(ws.send).mockRejectedValueOnce(new Error('WebSocket 连接已断开'))
    await ai.ask('查用户', null)
    expect(ai.generating).toBe(false)
    expect(ai.messages[1].streaming).toBe(false)
    expect(ai.messages[1].text).toContain('请求失败')
  })

  it('ask:服务端兜底 error 事件按终态收尾', async () => {
    const ai = useAiStore()
    vi.mocked(ws.send).mockImplementationOnce(async (_t, _p, onEvent) => {
      onEvent({ id: 'r', event: 'error', data: { message: '连接已断开,请重试' } })
      return 'r'
    })
    await ai.ask('查用户', null)
    expect(ai.generating).toBe(false)
    expect(ai.messages[1].streaming).toBe(false)
    expect(ai.messages[1].text).toContain('连接已断开')
    expect(ws.done).toHaveBeenCalledWith('r')
  })

  it('ask:新建会话失败时抛出(调用方提示并恢复输入),不留下 generating 卡死', async () => {
    vi.mocked(post).mockRejectedValueOnce(new Error('Network Error'))
    const ai = useAiStore()
    await expect(ai.ask('查用户', null)).rejects.toThrow('会话创建失败:Network Error')
    expect(ai.generating).toBe(false)
    expect(ai.messages).toHaveLength(0)
  })

  it('cancelAsk:摘掉 WS handler 并复位状态,取消后到达的事件被丢弃', async () => {
    const ai = useAiStore()
    let emit: (ev: any) => void = () => {}
    vi.mocked(ws.send).mockImplementationOnce(async (_t, _p, onEvent) => { emit = onEvent; return 'r' })
    await ai.ask('查用户', null)
    emit({ id: 'r', event: 'ai.token', data: { delta: 'SELECT' } })
    expect(ai.generating).toBe(true)

    ai.cancelAsk()
    expect(ai.generating).toBe(false)
    expect(ai.messages[1].streaming).toBe(false)
    expect(ws.done).toHaveBeenCalledWith('r')

    emit({ id: 'r', event: 'ai.token', data: { delta: ' 1' } })   // 取消后的迟到事件
    emit({ id: 'r', event: 'ai.done', data: { sql: 'SELECT 1' } })
    expect(ai.messages[1].text).toBe('SELECT')                    // 不再被追加
    expect(ai.messages[1].sql).toBeNull()
  })

  it('cancelAsk:建连握手期间取消(此时 reqId 还没就绪),旧请求的终态事件不影响后续 ask', async () => {
    const ai = useAiStore()
    let emitOld: (ev: any) => void = () => {}
    let releaseOld: (id: string) => void = () => {}
    // send 卡在建连握手:reqId 要等它返回才存在,取消会落在这个窗口里
    vi.mocked(ws.send).mockImplementationOnce((_t, _p, onEvent) => {
      emitOld = onEvent
      return new Promise<string>(res => { releaseOld = res })
    })
    const asking = ai.ask('查用户', null)
    await new Promise(r => setTimeout(r, 0))     // 等 ask 走到 await ws.send
    expect(ai.generating).toBe(true)

    ai.cancelAsk()
    expect(ai.generating).toBe(false)
    expect(ai.messages[1].streaming).toBe(false)
    expect(ai.messages[1].text).toBe('(已取消)')

    releaseOld('r-old')                          // 握手完成:必须立刻补走取消路径摘掉 handler
    await asking
    expect(ws.done).toHaveBeenCalledWith('r-old')

    // 新请求开始后,旧请求的终态事件迟到
    let emitNew: (ev: any) => void = () => {}
    vi.mocked(ws.send).mockImplementationOnce(async (_t, _p, onEvent) => { emitNew = onEvent; return 'r-new' })
    await ai.ask('再查一次', null)
    expect(ai.generating).toBe(true)

    emitOld({ id: 'r-old', event: 'ai.done', data: { sql: 'SELECT old' } })
    expect(ai.generating).toBe(true)             // 新请求仍应在生成中
    const last = ai.messages[ai.messages.length - 1]
    expect(last.streaming).toBe(true)
    expect(last.sql).toBeNull()

    emitNew({ id: 'r-new', event: 'ai.done', data: { sql: 'SELECT new' } })
    expect(ai.generating).toBe(false)
    expect(ai.messages[ai.messages.length - 1].sql).toBe('SELECT new')
  })

  it('cancelAsk:生成中切换过会话时,不改动新会话里的消息', async () => {
    const ai = useAiStore()
    let emit: (ev: any) => void = () => {}
    vi.mocked(ws.send).mockImplementationOnce(async (_t, _p, onEvent) => { emit = onEvent; return 'r' })
    await ai.ask('查用户', null)
    // 切到别的会话:消息列表整体换掉,下标 1 已不是发起时的那条
    vi.mocked(get).mockResolvedValueOnce({ items: [
      { id: 'm8', role: 'user', content: '别的问题' },
      { id: 'm9', role: 'assistant', content: JSON.stringify({ text: '别的回答', sql: null }) },
    ] })
    await ai.selectSession('s2')

    ai.cancelAsk()
    expect(ai.generating).toBe(false)
    expect(ai.messages[1].text).toBe('别的回答')
    expect(ai.messages[1].streaming).toBeUndefined()

    emit({ id: 'r', event: 'ai.done', data: { sql: 'SELECT 1' } })
    expect(ai.messages[1].text).toBe('别的回答')
    expect(ai.messages[1].sql).toBeNull()
  })

  it('ask:生成中切换会话,迟到的流事件不污染新会话', async () => {
    const ai = useAiStore()
    let emit: (ev: any) => void = () => {}
    vi.mocked(ws.send).mockImplementationOnce(async (_t, _p, onEvent) => { emit = onEvent; return 'r' })
    await ai.ask('查用户', null)
    vi.mocked(get).mockResolvedValueOnce({ items: [{ id: 'm9', role: 'user', content: '别的会话' }] })
    await ai.selectSession('s2')
    emit({ id: 'r', event: 'ai.token', data: { delta: 'X' } })
    expect(ai.messages).toHaveLength(1)
    expect(ai.messages[0].text).toBe('别的会话')
    emit({ id: 'r', event: 'ai.done', data: { sql: 'SELECT 1' } })
    expect(ai.generating).toBe(false)   // 状态仍要复位
  })
})

describe('ai store · 自助查表工具轨迹', () => {
  it('ask:ai.tool 事件按 call_id 合并,ai.done 以服务端轨迹为准', async () => {
    const trace = [{ call_id: 'c1', name: 'describe_table', status: 'done',
                     summary: '查看 users 表结构' }]
    vi.mocked(ws.send).mockImplementationOnce(async (_t, _p, onEvent) => {
      onEvent({ id: 'r', event: 'ai.tool',
                data: { call_id: 'c1', name: 'describe_table', status: 'running' } })
      onEvent({ id: 'r', event: 'ai.tool',
                data: { call_id: 'c1', name: 'describe_table', status: 'done',
                        summary: '查看 users 表结构' } })
      onEvent({ id: 'r', event: 'ai.done', data: { text: '好', sql: 'SELECT 1', tools: trace } })
      return 'r'
    })
    const ai = useAiStore()
    await ai.ask('查用户', null)
    const bot = ai.messages[1]
    expect(bot.tools).toHaveLength(1)
    expect(bot.tools![0]).toMatchObject({ call_id: 'c1', status: 'done',
                                          summary: '查看 users 表结构' })
  })

  it('selectSession:历史消息解析 tools 字段,旧数据缺省为 undefined', async () => {
    const ai = useAiStore()
    vi.mocked(get).mockResolvedValueOnce({ items: [
      { id: 'm1', role: 'assistant',
        content: JSON.stringify({ text: '好', sql: 'SELECT 1',
                                  tools: [{ name: 'list_tables', status: 'done' }] }) },
      { id: 'm2', role: 'assistant', content: JSON.stringify({ text: '旧', sql: null }) },
    ] })
    await ai.selectSession('s1')
    expect(ai.messages[0].tools?.[0].name).toBe('list_tables')
    expect(ai.messages[1].tools).toBeUndefined()
  })
})
