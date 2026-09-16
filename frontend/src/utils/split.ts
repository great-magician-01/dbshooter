/** 分隔条拖拽缩放:pointer 捕获 + 尺寸钳制 + localStorage 记忆。 */
import { getCurrentScope, onScopeDispose, ref } from 'vue'

export interface SplitterOptions {
  /** 拖拽轴:x = 左右分栏(调宽度),y = 上下分栏(调高度) */
  axis: 'x' | 'y'
  /** 被调面板相对分隔条的位置:'start'(左/上,默认)正向拖拽放大;'end'(右/下)正向拖拽缩小 */
  side?: 'start' | 'end'
  min: number
  /** 固定值,或在拖拽开始时求值(如按容器剩余空间限制) */
  max: number | (() => number)
  /** localStorage 键;传入则拖拽结束后记忆尺寸 */
  storageKey?: string
}

/** 可拖拽尺寸:返回 size(面板宽/高 px)与分隔条的 pointerdown 处理器 */
export function useSplitter(initial: number, opts: SplitterOptions) {
  const { axis, side = 'start', min, storageKey } = opts
  const stored = storageKey ? Number(localStorage.getItem(storageKey)) : NaN
  const hasStored = Number.isFinite(stored) && stored > 0
  const size = ref(
    hasStored
      ? (typeof opts.max === 'number' ? Math.min(opts.max, Math.max(min, stored)) : Math.max(min, stored))
      : initial,
  )

  let stopDrag: (() => void) | null = null

  function onPointerDown(e: PointerEvent) {
    if (e.button !== 0 || stopDrag) return
    e.preventDefault()
    const maxNow = typeof opts.max === 'function' ? opts.max() : opts.max
    const startPos = axis === 'x' ? e.clientX : e.clientY
    const startSize = size.value
    const sign = side === 'end' ? -1 : 1
    const cursor = axis === 'x' ? 'col-resize' : 'row-resize'

    const onMove = (ev: PointerEvent) => {
      const pos = axis === 'x' ? ev.clientX : ev.clientY
      size.value = Math.min(maxNow, Math.max(min, Math.round(startSize + (pos - startPos) * sign)))
    }
    const onUp = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      stopDrag = null
      if (storageKey) localStorage.setItem(storageKey, String(size.value))
    }
    stopDrag = onUp
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    // 拖动全程保持拖拽光标并禁止文本选中(指针可能悬停在编辑器/网格上)
    document.body.style.cursor = cursor
    document.body.style.userSelect = 'none'
  }

  // 组件在拖拽中途卸载时兜底移除监听
  if (getCurrentScope()) onScopeDispose(() => stopDrag?.())

  return { size, onPointerDown }
}
