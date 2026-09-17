/** api/ws:单连接多路复用;断线必须通知在途请求,否则 query.done / ai.done 永不到达、前端状态卡死。 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { WsClient } from '@/api/ws'

/** 最小 WebSocket 替身:手动控制 open / close,便于构造断线场景 */
class FakeSocket {
  static OPEN = 1
  static instances: FakeSocket[] = []
  readyState = 0
  sent: string[] = []
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null

  constructor(public url: string) { FakeSocket.instances.push(this) }
  send(data: string) { this.sent.push(data) }
  open() { this.readyState = FakeSocket.OPEN; this.onopen?.() }
  close() { this.readyState = 3; this.onclose?.() }
}

beforeEach(() => {
  FakeSocket.instances = []
  vi.stubGlobal('WebSocket', FakeSocket)
})

afterEach(() => { vi.unstubAllGlobals() })

describe('WsClient', () => {
  it('send:连接就绪后按 id 路由事件', async () => {
    const c = new WsClient()
    const events: any[] = []
    const p = c.send('query.execute', { conn_id: 'c1' }, ev => events.push(ev))
    const sock = FakeSocket.instances[0]
    sock.open()
    await p
    expect(sock.url).toContain('/ws')
    expect(sock.sent).toHaveLength(1)
    const req = JSON.parse(sock.sent[0])
    expect(req.type).toBe('query.execute')
    sock.onmessage?.({ data: JSON.stringify({ id: req.id, event: 'query.done', data: {} }) })
    expect(events).toHaveLength(1)
    expect(events[0].event).toBe('query.done')
  })

  it('断线:合成为 error 事件通知所有在途 handler,并清空注册表', async () => {
    const c = new WsClient()
    const a: any[] = []
    const b: any[] = []
    const pa = c.send('query.execute', {}, ev => a.push(ev))
    const sock = FakeSocket.instances[0]
    sock.open()
    await pa
    const pb = c.send('ai.text2sql', {}, ev => b.push(ev))
    await pb

    sock.close()                       // 服务重启 / token 失配 4401
    for (const ev of [...a, ...b]) {
      expect(ev.event).toBe('error')
      expect(ev.data.message).toContain('连接已断开')
    }
    expect(a).toHaveLength(1)
    expect(b).toHaveLength(1)

    // 注册表已清空:同 id 的迟到消息不会再回调(handler 无泄漏)
    const lateId = JSON.parse(sock.sent[0]).id
    sock.onmessage?.({ data: JSON.stringify({ id: lateId, event: 'query.done', data: {} }) })
    expect(a).toHaveLength(1)
  })

  it('握手阶段就断开(未 accept):send 直接失败,不永久挂起', async () => {
    const c = new WsClient()
    const p = c.send('ai.text2sql', {}, () => {})
    FakeSocket.instances[0].close()    // 服务端 close(4401) 不会触发 onopen
    await expect(p).rejects.toThrow('连接已断开')
  })

  it('断线后再次 send 会重新建连', async () => {
    const c = new WsClient()
    const p1 = c.send('query.execute', {}, () => {})
    const first = FakeSocket.instances[0]
    first.open()
    await p1
    first.close()

    const p2 = c.send('query.execute', {}, () => {})
    expect(FakeSocket.instances).toHaveLength(2)
    FakeSocket.instances[1].open()
    await expect(p2).resolves.toBeTruthy()
    expect(FakeSocket.instances[1].sent).toHaveLength(1)
  })
})
