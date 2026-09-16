/** utils/contextMenu 单例状态单测(jsdom)。 */
import { afterEach, describe, expect, it } from 'vitest'

import { closeContextMenu, ctxMenuState, showContextMenu } from '@/utils/contextMenu'

afterEach(() => closeContextMenu())

describe('contextMenu', () => {
  it('showContextMenu:记录坐标与菜单项并打开', () => {
    showContextMenu({ clientX: 100, clientY: 50 },
      [{ label: '新建 SQL 标签页', action: () => {} }])
    expect(ctxMenuState.visible).toBe(true)
    expect(ctxMenuState.x).toBe(100)
    expect(ctxMenuState.items).toHaveLength(1)
  })

  it('贴边收拢:坐标超出视口时被夹回可见范围', () => {
    showContextMenu({ clientX: 10000, clientY: 10000 },
      [{ label: 'a', action: () => {} }])
    expect(ctxMenuState.x).toBeLessThanOrEqual(window.innerWidth - 8)
    expect(ctxMenuState.y).toBeLessThanOrEqual(window.innerHeight - 8)
  })

  it('空菜单不打开;closeContextMenu 关闭', () => {
    showContextMenu({ clientX: 10, clientY: 10 }, [])
    expect(ctxMenuState.visible).toBe(false)
    showContextMenu({ clientX: 10, clientY: 10 }, [{ label: 'a', action: () => {} }])
    closeContextMenu()
    expect(ctxMenuState.visible).toBe(false)
  })
})
