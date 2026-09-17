/** 组件级:SqlPane 的执行代次守卫(取消后重跑不被旧事件覆盖)与截断结果提示。 */
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick, reactive } from 'vue'

import type { Tab } from '@/types'

vi.mock('@/api/http', () => ({
  get: vi.fn(async () => ({ rows: [], has_more: false, truncated: false, total_buffered: 0 })),
  post: vi.fn(async () => ({ results: [{ rows: [] }] })),
}))

// 捕获每次 send 的 handler,由测试手动回放事件(真实事件顺序不可控)
const { sent, wsMock } = vi.hoisted(() => {
  const sent: { id: string; onEvent: (ev: any) => void }[] = []
  return {
    sent,
    wsMock: {
      send: async (_type: string, _payload: any, onEvent: (ev: any) => void) => {
        const id = `r${sent.length}`
        sent.push({ id, onEvent })
        return id
      },
      done: () => {},
    },
  }
})
vi.mock('@/api/ws', () => ({ ws: wsMock }))

import SqlPane from '@/components/panes/SqlPane.vue'

const rowsEvent = (rows: any[][], extra: Record<string, any> = {}) => ({
  id: 'r0', event: 'query.rows', data: { columns: [{ name: 'a', type: 'int' }], rows, ...extra },
})

function makeTab(): Tab {
  return reactive({ id: 't1', type: 'sql', title: 'SQL-1', connection_id: 'c1',
                    context: {}, content: 'SELECT 1', sort: 0 }) as Tab
}

function mountPane(): VueWrapper {
  return mount(SqlPane, { props: { tab: makeTab() }, global: { stubs: { CodeEditor: true } } })
}

const cancelBtn = (w: VueWrapper) => w.findAll('.pt-btn').find(b => b.text() === '取消')

beforeEach(() => {
  setActivePinia(createPinia())
  sent.length = 0
})

describe('SqlPane · 执行代次守卫', () => {
  it('取消后立刻重跑:旧执行的 query.done 被丢弃,不覆盖新执行的状态', async () => {
    const w = mountPane()
    await (w.vm as any).run()
    expect(sent).toHaveLength(1)
    const first = sent[0]
    first.onEvent({ id: first.id, event: 'query.started', data: { query_id: 'q1' } })
    first.onEvent(rowsEvent([['old']], { has_more: true }))
    await nextTick()
    expect(w.text()).toContain('old')

    // 取消(执行代次 +1)→ 立刻重跑(再 +1),新执行清空了结果集
    await cancelBtn(w)!.trigger('click')
    await (w.vm as any).run()
    await nextTick()
    expect(sent).toHaveLength(2)
    expect(w.text()).toContain('执行 SQL 后在此展示结果集')

    // 旧执行的终态事件现在才到:必须被丢弃,否则 statusText / running 会被旧结果改写
    first.onEvent({ id: first.id, event: 'query.done', data: { row_count: 999, elapsed_ms: 9 } })
    await nextTick()
    expect(w.find('.result-status').text()).toBe('执行中…')
    expect(cancelBtn(w)).toBeTruthy()              // 仍在执行中,取消按钮还在
    expect(w.text()).not.toContain('999')

    // 当前执行的事件照常生效
    sent[1].onEvent({ id: sent[1].id, event: 'query.done',
      data: { row_count: 3, elapsed_ms: 4, summary: [{ kind: 'rows', affected: null, error: null }] } })
    await nextTick()
    expect(w.find('.result-status').text()).toContain('3 行')
    expect(cancelBtn(w)).toBeUndefined()           // running 已复位
  })

  it('取消:状态栏置为「已取消」,不停在「执行中…」', async () => {
    const w = mountPane()
    await (w.vm as any).run()
    sent[0].onEvent({ id: sent[0].id, event: 'query.started', data: { query_id: 'q1' } })
    await nextTick()
    await cancelBtn(w)!.trigger('click')
    await nextTick()
    expect(w.find('.result-status').text()).toBe('已取消')
    expect(cancelBtn(w)).toBeUndefined()
  })

  it('has_more=true && truncated=true:仍显示「加载更多」(缓冲区内还有行)', async () => {
    const w = mountPane()
    await (w.vm as any).run()
    sent[0].onEvent(rowsEvent([['1']], { has_more: true, truncated: true }))
    await nextTick()
    expect(w.text()).toContain('加载更多')
    expect(w.text()).not.toContain('已达 2000 行缓冲上限')
  })

  it('has_more=false && truncated=true:隐藏「加载更多」,提示缓冲上限', async () => {
    const w = mountPane()
    await (w.vm as any).run()
    sent[0].onEvent(rowsEvent([['1']], { has_more: false, truncated: true }))
    await nextTick()
    expect(w.text()).toContain('已达 2000 行缓冲上限,请加 LIMIT 缩小结果')
    expect(w.text()).not.toContain('加载更多')
  })

  it('缓冲区内还有行(未截断)时显示「加载更多」', async () => {
    const w = mountPane()
    await (w.vm as any).run()
    sent[0].onEvent(rowsEvent([['1']], { has_more: true, truncated: false }))
    await nextTick()
    expect(w.text()).toContain('加载更多')
    expect(w.text()).not.toContain('已达 2000 行缓冲上限')
  })
})
