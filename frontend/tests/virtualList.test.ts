/** utils/virtualList 纯逻辑单测:窗口计算与列宽估算。 */
import { describe, expect, it } from 'vitest'
import { ref } from 'vue'

import { estimateColWidths, useVirtualList } from '@/utils/virtualList'

function fakeScroll(scrollTop: number, clientHeight: number): Event {
  return { target: { scrollTop, clientHeight } } as unknown as Event
}

describe('useVirtualList', () => {
  it('数据量未超阈值:全量渲染,无占位', () => {
    const items = ref(Array.from({ length: 300 }, (_, i) => i))
    const v = useVirtualList(items, { threshold: 500 })
    expect(v.virtual.value).toBe(false)
    expect(v.visible.value).toHaveLength(300)
    expect(v.visible.value[0]).toEqual({ item: 0, index: 0 })
    expect(v.visible.value[299].index).toBe(299)
    expect(v.topPad.value).toBe(0)
    expect(v.bottomPad.value).toBe(0)
  })

  it('恰好等于阈值不虚拟化,超过才启用', () => {
    const items = ref(Array.from({ length: 500 }, (_, i) => i))
    const v = useVirtualList(items, { threshold: 500 })
    expect(v.virtual.value).toBe(false)
    items.value.push(500)
    expect(v.virtual.value).toBe(true)
  })

  it('虚拟模式:按滚动位置渲染窗口(含 overscan),上下占位补偿总高度', () => {
    const items = ref(Array.from({ length: 2000 }, (_, i) => i))
    const v = useVirtualList(items, { rowHeight: 30, threshold: 500, overscan: 10 })
    v.onScroll(fakeScroll(3000, 300))   // 视口覆盖第 100~110 行
    expect(v.virtual.value).toBe(true)
    const w = v.visible.value
    expect(w[0].index).toBe(90)                    // 100 - overscan
    expect(w[w.length - 1].index).toBe(119)        // end=110+10 为开区间
    expect(v.topPad.value).toBe(90 * 30)
    expect(v.bottomPad.value).toBe((2000 - 120) * 30)
  })

  it('边界:顶部 clamp 到 0,底部 clamp 到行数且窗口非空', () => {
    const items = ref(Array.from({ length: 1000 }, (_, i) => i))
    const v = useVirtualList(items, { rowHeight: 30, threshold: 100, overscan: 10 })
    v.onScroll(fakeScroll(0, 300))
    expect(v.visible.value[0].index).toBe(0)
    v.onScroll(fakeScroll(999999, 300))            // 滚到底之外
    const w = v.visible.value
    expect(w[w.length - 1].index).toBe(999)
    expect(v.bottomPad.value).toBe(0)
    expect(w.length).toBeGreaterThan(0)
  })

  it('syncViewport:无滚动事件时同步视口尺寸', () => {
    const items = ref(Array.from({ length: 1000 }, (_, i) => i))
    const v = useVirtualList(items, { rowHeight: 30, threshold: 100, overscan: 0 })
    v.syncViewport({ scrollTop: 300, clientHeight: 300 } as HTMLElement)
    expect(v.visible.value[0].index).toBe(10)
    v.syncViewport(null)                           // 空值安全
    expect(v.visible.value[0].index).toBe(10)
  })
})

describe('estimateColWidths', () => {
  it('取表头与取样单元格的最大长度并留白', () => {
    const w = estimateColWidths(['id', 'name'], [['1', 'alice'], ['22', 'bob']])
    expect(w[0]).toBe(Math.max(6, 2 + 2))          // '22' → 4,夹到 min 6
    expect(w[1]).toBe(5 + 2)                       // 'alice'
  })

  it('clamp 到 [min, max]', () => {
    const w = estimateColWidths(['x'], [['a'.repeat(200)]], 6, 50)
    expect(w[0]).toBe(50)
    expect(estimateColWidths([''], [[]])[0]).toBe(6)
  })

  it('缺失的单元格按空处理', () => {
    // 'only-one'(8 字符)落在第 1 列,第 2 列无数据只剩表头
    const w = estimateColWidths(['a', 'b'], [['only-one']])
    expect(w).toEqual([10, 6])
  })
})
