/** 极简全局提示(替代组件库 message,与设计系统一致)。 */
let host: HTMLElement | null = null

export function toast(msg: string, kind: 'ok' | 'err' | 'info' = 'info') {
  if (!host) {
    host = document.createElement('div')
    host.className = 'toasts'
    document.body.appendChild(host)
  }
  const el = document.createElement('div')
  el.className = `toast ${kind === 'info' ? '' : kind}`
  el.textContent = msg
  host.appendChild(el)
  setTimeout(() => {
    el.style.transition = 'opacity .3s'
    el.style.opacity = '0'
    setTimeout(() => el.remove(), 320)
  }, 2600)
}
