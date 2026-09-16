/** utils/scroll 纯函数单测。 */
import { describe, expect, it } from 'vitest'

import { nearBottom } from '@/utils/scroll'

describe('nearBottom', () => {
  it('接近底部触发,远离底部不触发', () => {
    const el = { scrollTop: 0, clientHeight: 300, scrollHeight: 1000 }
    expect(nearBottom(el)).toBe(false)
    expect(nearBottom({ ...el, scrollTop: 540 })).toBe(true)   // 540+300 = 840 = 1000-160,恰好到阈值
    expect(nearBottom({ ...el, scrollTop: 539 })).toBe(false)
    expect(nearBottom({ ...el, scrollTop: 700 })).toBe(true)   // 已在底部
  })

  it('内容不足一屏(无滚动条)时视为在底部', () => {
    expect(nearBottom({ scrollTop: 0, clientHeight: 300, scrollHeight: 200 })).toBe(true)
    expect(nearBottom({ scrollTop: 0, clientHeight: 0, scrollHeight: 0 })).toBe(true)
  })

  it('自定义阈值', () => {
    const el = { scrollTop: 600, clientHeight: 300, scrollHeight: 1000 }
    expect(nearBottom(el, 50)).toBe(false)
    expect(nearBottom(el, 100)).toBe(true)
  })
})
