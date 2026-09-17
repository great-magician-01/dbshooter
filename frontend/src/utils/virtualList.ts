/** 虚拟列表:固定行高窗口渲染 + 上下占位。数据量超过阈值才启用,少量数据退化为全量渲染。 */
import { computed, ref, type Ref } from 'vue'

export interface VirtualRow<T> {
  item: T
  index: number   // 原始下标,供 key 与行号使用
}

export function useVirtualList<T>(items: Ref<T[]>, opts: {
  rowHeight?: number   // 初始行高(px),挂载后应用实测值修正
  threshold?: number   // 超过该行数才启用虚拟化
  overscan?: number    // 窗口上下多渲染的行数,减少快速滚动时的白屏
} = {}) {
  const rowH = ref(opts.rowHeight ?? 29)
  const threshold = opts.threshold ?? 500
  const overscan = opts.overscan ?? 12
  const scrollTop = ref(0)
  const viewportH = ref(0)

  /** 数据量超过阈值才虚拟化;少量数据全量渲染,与普通表格行为一致 */
  const virtual = computed(() => items.value.length > threshold)

  const start = computed(() => {
    if (!virtual.value) return 0
    const s = Math.floor(scrollTop.value / rowH.value) - overscan
    return Math.max(0, Math.min(s, Math.max(0, items.value.length - 1)))
  })
  const end = computed(() => {
    if (!virtual.value) return items.value.length
    const e = Math.ceil((scrollTop.value + viewportH.value) / rowH.value) + overscan
    return Math.min(items.value.length, Math.max(e, start.value))
  })

  /** 当前渲染窗口(非虚拟模式 = 全量) */
  const visible = computed<VirtualRow<T>[]>(() => {
    const out: VirtualRow<T>[] = []
    for (let i = start.value; i < end.value; i++) out.push({ item: items.value[i], index: i })
    return out
  })

  const topPad = computed(() => virtual.value ? start.value * rowH.value : 0)
  const bottomPad = computed(() =>
    virtual.value ? Math.max(0, (items.value.length - end.value) * rowH.value) : 0)

  /** 滚动容器事件:同步滚动位置与视口高度 */
  function onScroll(e: Event) {
    const el = e.target as HTMLElement
    scrollTop.value = el.scrollTop
    viewportH.value = el.clientHeight
  }

  /** 非滚动场景(挂载完成等)同步一次视口尺寸 */
  function syncViewport(el: HTMLElement | null | undefined) {
    if (!el) return
    scrollTop.value = el.scrollTop
    viewportH.value = el.clientHeight
  }

  /**
   * 监听容器尺寸变化(分隔条拖拽 / 窗口缩放 / 面板收起):视口高度变了必须重算渲染窗口,
   * 否则会大片留白或漏渲染。返回清理函数,调用方须在卸载时执行(断开 observer,避免泄漏)。
   * 无 ResizeObserver 的环境(老浏览器 / jsdom)退化为只在滚动时同步。
   */
  function observeViewport(el: HTMLElement | null | undefined): () => void {
    if (!el || typeof ResizeObserver === 'undefined') return () => {}
    syncViewport(el)
    const ro = new ResizeObserver(() => syncViewport(el))
    ro.observe(el)
    return () => ro.disconnect()
  }

  return { rowH, virtual, visible, topPad, bottomPad, onScroll, syncViewport, observeViewport }
}

/**
 * 估算各列渲染宽度(ch,等宽字体):取表头文本与前若干行单元格文本的最大长度,
 * 结果夹在 [min, max] 之间。用于虚拟模式的 table-layout:fixed,避免窗口切换时列宽跳动。
 * 调用方应固定取样范围(如前 50 行),保证数据追加后宽度稳定。
 */
export function estimateColWidths(
  headers: string[], sample: string[][], min = 6, max = 50,
): number[] {
  return headers.map((h, j) => {
    let w = h.length
    for (const row of sample) {
      const len = row[j]?.length ?? 0
      if (len > w) w = len
    }
    return Math.min(max, Math.max(min, w + 2))
  })
}
