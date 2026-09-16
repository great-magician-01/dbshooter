/** utils/split 分隔条拖拽单测:jsdom 下用 MouseEvent 模拟 pointer 序列。 */
import { beforeEach, describe, expect, it } from 'vitest'

import { useSplitter } from '@/utils/split'

function down(handler: (e: any) => void, pos: number) {
  handler(new MouseEvent('pointerdown', { clientX: pos, clientY: pos, button: 0 }))
}
function move(pos: number) {
  window.dispatchEvent(new MouseEvent('pointermove', { clientX: pos, clientY: pos }))
}
function up() {
  window.dispatchEvent(new MouseEvent('pointerup'))
}

describe('useSplitter', () => {
  beforeEach(() => localStorage.clear())

  it('start 侧(左栏):向右拖增大宽度,松开后写入 localStorage', () => {
    const { size, onPointerDown } = useSplitter(264, { axis: 'x', min: 180, max: 560, storageKey: 'k1' })
    down(onPointerDown, 300)
    move(350)
    expect(size.value).toBe(314)
    up()
    expect(localStorage.getItem('k1')).toBe('314')
  })

  it('end 侧(右栏):向右拖缩小宽度', () => {
    const { size, onPointerDown } = useSplitter(360, { axis: 'x', side: 'end', min: 280, max: 680 })
    down(onPointerDown, 1000)
    move(1080)
    expect(size.value).toBe(280)
  })

  it('尺寸钳制在 [min, max]', () => {
    const { size, onPointerDown } = useSplitter(264, { axis: 'x', min: 180, max: 560 })
    down(onPointerDown, 300)
    move(3000)
    expect(size.value).toBe(560)
    move(-3000)
    expect(size.value).toBe(180)
    up()
  })

  it('y 轴 + max 函数:按容器剩余空间钳制', () => {
    const { size, onPointerDown } = useSplitter(280, { axis: 'y', side: 'end', min: 110, max: () => 400 })
    down(onPointerDown, 500)
    move(100) // 向上拖 400 → 280+400=680,钳到 400
    expect(size.value).toBe(400)
    up()
  })

  it('读取已记忆的尺寸作为初始值', () => {
    localStorage.setItem('k2', '333')
    const { size } = useSplitter(264, { axis: 'x', min: 180, max: 560, storageKey: 'k2' })
    expect(size.value).toBe(333)
  })

  it('非左键按下不启动拖拽', () => {
    const { size, onPointerDown } = useSplitter(264, { axis: 'x', min: 180, max: 560 })
    onPointerDown(new MouseEvent('pointerdown', { clientX: 300, button: 2 }) as any)
    move(500)
    expect(size.value).toBe(264)
  })
})
