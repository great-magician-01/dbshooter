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
  /** 服务端已发起关闭(CLOSING)但 onclose 还没派发 */
  closing() { this.readyState = 2 }
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

  it('陈旧 socket:迟到的 onclose 不得 reject 新 opening,也不得误杀新连接的在途请求', async () => {
    const c = new WsClient()
    const a: any[] = []
    const p1 = c.send('query.execute', {}, ev => a.push(ev))
    const sockA = FakeSocket.instances[0]
    sockA.open()
    await p1
    sockA.close()                       // 服务重启:旧连接在途请求收到 error
    expect(a).toHaveLength(1)

    const b: any[] = []
    const p2 = c.send('ai.text2sql', {}, ev => b.push(ev))
    const sockB = FakeSocket.instances[1]
    sockB.open()
    await expect(p2).resolves.toBeTruthy()
    // 真实路径:新连接已建立、opening 已被清空之后,旧 socket 的 onclose 才派发
    sockA.onclose?.()
    expect(b).toHaveLength(0)           // 新连接的在途请求没被误杀

    // 旧 socket 上的迟到消息同样不得路由到新连接的 handler
    sockA.onmessage?.({ data: JSON.stringify({ id: JSON.parse(sockB.sent[0]).id, event: 'query.done', data: {} }) })
    expect(b).toHaveLength(0)
  })

  it('服务端关闭中(CLOSING)又发新请求:旧连接的在途请求必须收到 error,不能被静默遗弃', async () => {
    const c = new WsClient()
    const a: any[] = []
    const p1 = c.send('query.execute', {}, ev => a.push(ev))
    const sockA = FakeSocket.instances[0]
    sockA.open()
    await p1

    sockA.closing()                     // 服务端已开始关闭,onclose 尚未派发
    const b: any[] = []
    const p2 = c.send('query.execute', {}, ev => b.push(ev))   // 只能另建新 socket
    const sockB = FakeSocket.instances[1]
    sockB.open()
    await expect(p2).resolves.toBeTruthy()
    expect(b).toHaveLength(0)

    sockA.onclose?.()                   // 迟到的旧 onclose:必须 fail 掉挂在旧 socket 上的请求
    expect(a).toHaveLength(1)
    expect(a[0].event).toBe('error')
    expect(a[0].data.message).toContain('连接已断开')

    // 新连接不受影响:它的请求仍在途,照常收到终态事件
    sockB.onmessage?.({ data: JSON.stringify({ id: JSON.parse(sockB.sent[0]).id, event: 'query.done', data: {} }) })
    expect(b).toHaveLength(1)
    expect(b[0].event).toBe('query.done')
  })

  it('心跳看门狗:静默超过 75s 主动断开并通知在途请求', async () => {
    vi.useFakeTimers()
    try {
      const c = new WsClient()
      const events: any[] = []
      const p = c.send('query.execute', {}, ev => events.push(ev))
      FakeSocket.instances[0].open()
      await p
      vi.advanceTimersByTime(60000)
      expect(events).toHaveLength(0)    // 75s 以内不误判
      vi.advanceTimersByTime(40000)     // 累计 100s 无任何消息
      expect(events).toHaveLength(1)
      expect(events[0].event).toBe('error')
      expect(events[0].data.message).toContain('连接已断开')
    } finally { vi.useRealTimers() }
  })

  it('心跳:服务端 _hb ping 刷新静默计时,不会误判断线', async () => {
    vi.useFakeTimers()
    try {
      const c = new WsClient()
      const events: any[] = []
      const p = c.send('query.execute', {}, ev => events.push(ev))
      const sock = FakeSocket.instances[0]
      sock.open()
      await p
      for (let i = 0; i < 4; i++) {
        vi.advanceTimersByTime(30000)
        sock.onmessage?.({ data: JSON.stringify({ id: '_hb', event: 'ping', data: {} }) })
      }
      expect(events).toHaveLength(0)    // 累计 120s 但每 30s 有心跳
    } finally { vi.useRealTimers() }
  })
})
