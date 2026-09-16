/** 全局右键菜单状态:单例(同一时刻至多一个,新开自动顶掉旧的)。 */
import { reactive } from 'vue'

export interface MenuItem {
  label: string
  icon?: string
  action: () => void
}

export const ctxMenuState = reactive<{ visible: boolean; x: number; y: number; items: MenuItem[] }>({
  visible: false, x: 0, y: 0, items: [],
})

/** 在鼠标位置打开菜单(贴边收拢,避免溢出视口)。 */
export function showContextMenu(e: { clientX: number; clientY: number }, items: MenuItem[]): void {
  if (!items.length) return
  const w = 200, ih = 30
  ctxMenuState.x = Math.max(8, Math.min(e.clientX, window.innerWidth - w - 8))
  ctxMenuState.y = Math.max(8, Math.min(e.clientY, window.innerHeight - items.length * ih - 16))
  ctxMenuState.items = items
  ctxMenuState.visible = true
}

export function closeContextMenu(): void {
  ctxMenuState.visible = false
}
