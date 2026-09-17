/** WebSocket 客户端:单连接多路复用,按请求 id 路由事件,断线自动重连 + 心跳看门狗。 */
import type { WsEvent } from '@/types'

type EventHandler = (ev: WsEvent) => void

/** 在途请求:记录它发在哪个 socket 上 —— socket 关闭时只 fail 属于它的请求 */
interface Pending {
  sock: WebSocket
  fn: EventHandler
}

/** 看门狗检查间隔与静默上限:服务端每 25s 推一次 _hb ping,75s 静默说明至少丢了两拍 */
const WATCHDOG_TICK_MS = 15000
const IDLE_LIMIT_MS = 75000
/** 断线时通知在途请求的统一文案(query.done / ai.done 永不到达会把前端状态卡死) */
const OFFLINE_MSG = '连接已断开,请重试'

export class WsClient {
  private socket: WebSocket | null = null
  private opening: Promise<void> | null = null
  private handlers = new Map<string, Pending>()
  private seq = 0
  /** 建连代次:每次真正建连自增,回调凭闭包捕获的代次判断自己是否已被新 socket 顶替 */
  private gen = 0
  /** 最近一次收到服务端消息的时间(任何消息,含 _hb 心跳),看门狗据此判断链路是否假死 */
  private lastSeen = 0
  private watchdog: ReturnType<typeof setInterval> | null = null

  private connect(): Promise<void> {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) return Promise.resolve()
    if (this.opening) return this.opening
    const token = localStorage.getItem('ds-token')
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const url = `${proto}://${location.host}/ws${token ? `?token=${encodeURIComponent(token)}` : ''}`
    const ws = new WebSocket(url)
    const myGen = ++this.gen
    this.opening = new Promise((resolve, reject) => {
      // 归属判断:自己已被更新的建连顶替时,回调不得碰 socket / opening / handlers,
      // 否则陈旧 socket 的 onclose 会 reject 新 opening、failAll 掉新连接的在途请求
      const stale = () => myGen !== this.gen
      ws.onopen = () => {
        if (stale()) return
        this.socket = ws
        this.opening = null
        this.startWatchdog()
        resolve()
      }
      ws.onerror = () => {
        if (stale()) return
        this.opening = null
        reject(new Error('WebSocket 连接失败'))
      }
      ws.onclose = () => {
        if (this.socket === ws) { this.socket = null; this.stopWatchdog() }
        // 属于这个 socket 的在途请求必须落地:即便它已被新连接顶替(服务端正在关闭、
        // onclose 还没派发时又发了新请求),否则这些请求永远收不到终态事件,调用方状态卡死
        this.failAll(OFFLINE_MSG, ws)
        if (stale()) return    // 陈旧 socket 不得 reject 新连接的 opening
        // 握手阶段就断开(服务重启 / 令牌失配 4401):连接 Promise 必须落地,否则 send 永久挂起
        if (this.opening) { this.opening = null; reject(new Error('WebSocket 连接已断开')) }
      }
      ws.onmessage = (e) => {
        let ev: WsEvent
        try { ev = JSON.parse(e.data) as WsEvent } catch { return }   // 忽略非协议消息
        if (this.socket === ws) this.lastSeen = Date.now()   // 任何消息(_hb 心跳)都算链路活着
        // 事件只认注册它的那个 socket:陈旧 socket 上迟到的消息不得路由到新连接的 handler。
        // _hb 心跳不属任何请求,查不到 handler 即为空操作。
        const item = this.handlers.get(ev.id)
        if (item && item.sock === ws) item.fn(ev)
      }
    })
    return this.opening
  }

  /**
   * 断线时通知在途请求(否则 query.done / ai.done 永不到达,前端状态卡死)。
   * 传 sock 时只通知发在那个 socket 上的请求,避免误杀新连接的在途请求。
   */
  private failAll(message: string, sock?: WebSocket) {
    const pending: [string, EventHandler][] = []
    for (const [id, item] of this.handlers) {
      if (sock && item.sock !== sock) continue
      pending.push([id, item.fn])
    }
    for (const [id, fn] of pending) {
      this.handlers.delete(id)
      fn({ id, event: 'error', data: { message } })
    }
  }

  private startWatchdog() {
    if (this.watchdog) return
    this.lastSeen = Date.now()
    this.watchdog = setInterval(() => this.checkAlive(), WATCHDOG_TICK_MS)
  }

  private stopWatchdog() {
    if (this.watchdog) { clearInterval(this.watchdog); this.watchdog = null }
  }

  /** 静默超限 = 链路假死(TCP 半开 / 服务端被挂起):本地主动断开并收尾,不干等可能永不来的 onclose */
  private checkAlive() {
    const sock = this.socket
    if (!sock) return
    if (Date.now() - this.lastSeen <= IDLE_LIMIT_MS) return
    this.socket = null
    this.stopWatchdog()
    try { sock.close() } catch { /* 关闭失败也无妨,下面照样通知在途请求 */ }
    this.failAll(OFFLINE_MSG, sock)
  }

  /** 发送一条请求,事件流通过 onEvent 回推;返回请求 id(用于取消) */
  async send(type: string, payload: any, onEvent: EventHandler): Promise<string> {
    await this.connect()
    const sock = this.socket
    if (!sock || sock.readyState !== WebSocket.OPEN)
      throw new Error('WebSocket 未连接,请重试')
    const id = `r${Date.now()}-${this.seq++}`
    this.handlers.set(id, { sock, fn: onEvent })
    sock.send(JSON.stringify({ id, type, payload }))
    return id
  }

  done(id: string) {
    this.handlers.delete(id)
  }
}

export const ws = new WsClient()
