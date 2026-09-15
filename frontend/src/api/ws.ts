/** WebSocket 客户端:单连接多路复用,按请求 id 路由事件,断线自动重连。 */
import type { WsEvent } from '@/types'

type EventHandler = (ev: WsEvent) => void

class WsClient {
  private socket: WebSocket | null = null
  private opening: Promise<void> | null = null
  private handlers = new Map<string, EventHandler>()
  private seq = 0

  private connect(): Promise<void> {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) return Promise.resolve()
    if (this.opening) return this.opening
    const token = localStorage.getItem('ds-token')
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const url = `${proto}://${location.host}/ws${token ? `?token=${encodeURIComponent(token)}` : ''}`
    this.opening = new Promise((resolve, reject) => {
      const ws = new WebSocket(url)
      ws.onopen = () => { this.socket = ws; this.opening = null; resolve() }
      ws.onerror = () => { this.opening = null; reject(new Error('WebSocket 连接失败')) }
      ws.onclose = () => { this.socket = null }
      ws.onmessage = (e) => {
        try {
          const ev = JSON.parse(e.data) as WsEvent
          this.handlers.get(ev.id)?.(ev)
        } catch { /* 忽略非协议消息 */ }
      }
    })
    return this.opening
  }

  /** 发送一条请求,事件流通过 onEvent 回推;返回请求 id(用于取消) */
  async send(type: string, payload: any, onEvent: EventHandler): Promise<string> {
    await this.connect()
    const id = `r${Date.now()}-${this.seq++}`
    this.handlers.set(id, onEvent)
    this.socket!.send(JSON.stringify({ id, type, payload }))
    return id
  }

  done(id: string) {
    this.handlers.delete(id)
  }
}

export const ws = new WsClient()
