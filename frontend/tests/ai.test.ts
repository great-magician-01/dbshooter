/** ai store:Provider 单生效切换 + 会话消息解析 + WS 流式问答(http/ws 全部 mock)。 */
import { createPinia, setActivePinia } from 'pinia'
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
