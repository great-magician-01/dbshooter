/** 组件级:TablePane 子页切换/结构表格渲染/ER 懒加载/错误占位(http 全 mock,子组件替身)。 */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/http', () => ({
  get: vi.fn(async (url: string) => {
    if (url.includes('/structure')) {
      return {
        columns: [
          { name: 'id', type: 'INTEGER', nullable: false, default: null,
            pk: 1, ordinal: 0, comment: '' },
          { name: 'name', type: 'TEXT', nullable: true, default: null,
            pk: 0, ordinal: 1, comment: '姓名' },
        ],
      }
    }
    if (url.includes('/ddl')) return { ddl: 'CREATE TABLE users (id INTEGER);' }
    return { relations: [] }
  }),
  post: vi.fn(async () => ({})),
}))

import { get } from '@/api/http'
import TablePane from '@/components/panes/TablePane.vue'
import type { Tab } from '@/types'

const tab: Tab = {
  id: 't1', type: 'table', title: 'users', connection_id: 'c1',
  context: { table: 'users', ref: 'users', path: 'main.users', kind: 'table' },
  content: '', sort: 0,
}

const DataPaneStub = { name: 'DataPane', props: ['tab'], template: '<div class="dp-stub">DATA</div>' }
const ErDiagramStub = { name: 'ErDiagram', props: ['tab'], template: '<div class="er-stub">ER</div>' }
const CodeEditorStub = {
  name: 'CodeEditor',
  // readonly 需显式 Boolean 类型:父组件裸写 readonly 传的是空串,
  // 数组形式 props 无类型不做布尔转换(真实组件 defineProps<boolean> 会转成 true)
  props: { modelValue: String, lang: String, readonly: Boolean },
  template: '<div class="ce-stub">{{ modelValue }}</div>',
}

function mountPane() {
  return mount(TablePane, {
    props: { tab },
    global: { stubs: { DataPane: DataPaneStub, ErDiagram: ErDiagramStub,
                       CodeEditor: CodeEditorStub } },
  })
}

/** 子页签条按文字点击:数据 | 结构 | DDL | ER */
async function clickSub(w: ReturnType<typeof mountPane>, label: string) {
  const target = w.findAll('.rt-tab').find(t => t.text().includes(label))!
  await target.trigger('click')
  await flushPromises()
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe('TablePane · 子页签', () => {
  it('默认数据子页;结构与 DDL 挂载即拉取;ER 未点击不挂载', async () => {
    const w = mountPane()
    await flushPromises()
    // 数据子页可见(v-show 未加 display:none)
    expect(w.find('.tp-sub').attributes('style') ?? '').not.toContain('display: none')
    // onMounted 拉结构与 DDL
    const urls = vi.mocked(get).mock.calls.map(c => c[0] as string)
    expect(urls.some(u => u.includes('/structure'))).toBe(true)
    expect(urls.some(u => u.includes('/ddl'))).toBe(true)
    // ER 懒加载:未点击前不挂载、自然也没有 relations 请求
    expect(w.find('.er-stub').exists()).toBe(false)
    expect(urls.some(u => u.includes('/relations'))).toBe(false)

    await clickSub(w, 'ER')
    expect(w.find('.er-stub').exists()).toBe(true)
  })

  it('结构子页:渲染列元数据表格(主键/可空/默认值/注释)', async () => {
    const w = mountPane()
    await flushPromises()
    await clickSub(w, '结构')
    const rows = w.findAll('tbody tr')
    expect(rows).toHaveLength(2)
    expect(rows[0].text()).toContain('id')
    expect(rows[0].text()).toContain('INTEGER')
    expect(rows[0].text()).toContain('NO')          // 不可空
    expect(rows[1].text()).toContain('name')
    expect(rows[1].text()).toContain('YES')
    expect(rows[1].text()).toContain('姓名')
    // 默认值 null 显示 NULL 占位
    expect(w.findAll('.null').length).toBeGreaterThan(0)
  })

  it('DDL 子页:只读编辑器展示 DDL 文本', async () => {
    const w = mountPane()
    await flushPromises()
    await clickSub(w, 'DDL')
    const ce = w.findComponent(CodeEditorStub)
    expect(ce.exists()).toBe(true)
    expect(ce.props('modelValue')).toContain('CREATE TABLE users')
    expect(ce.props('readonly')).toBe(true)
  })

  it('子页切换保活:切走 display:none 隐藏但不卸载,切回恢复', async () => {
    const w = mountPane()
    await flushPromises()
    const styleOf = () => w.find('.tp-sub').attributes('style') ?? ''
    expect(styleOf()).not.toContain('display: none')
    await clickSub(w, '结构')
    expect(styleOf()).toContain('display: none')
    // v-show:DOM 仍在,DataPane 未卸载重查
    expect(w.find('.dp-stub').exists()).toBe(true)
    await clickSub(w, '数据')
    expect(styleOf()).not.toContain('display: none')
  })

  it('结构加载失败:显示错误占位而不是空表格', async () => {
    vi.mocked(get).mockImplementation(async (url: string) => {
      if (url.includes('/structure')) throw new Error('表不存在')
      if (url.includes('/ddl')) return { ddl: '' }
      return { relations: [] }
    })
    const w = mountPane()
    await flushPromises()
    await clickSub(w, '结构')
    expect(w.text()).toContain('表不存在')
    expect(w.findAll('tbody tr')).toHaveLength(0)
    // DDL 为空 → 占位文案
    await clickSub(w, 'DDL')
    expect(w.text()).toContain('该对象没有 DDL')
  })

  it('结构与 DDL 的加载标志独立:结构先到时结构页不等 DDL', async () => {
    // DDL 请求永不 resolve:结构页不该一直停在"加载中…"
    let releaseDdl: (v: { ddl: string }) => void = () => {}
    vi.mocked(get).mockImplementation((url: string) => {
      if (url.includes('/structure')) {
        return Promise.resolve({ columns: [{ name: 'id', type: 'INTEGER', nullable: false,
                                             default: null, pk: 1, ordinal: 0, comment: '' }] })
      }
      if (url.includes('/ddl')) {
        return new Promise(res => { releaseDdl = res })
      }
      return Promise.resolve({ relations: [] })
    })
    const w = mountPane()
    await flushPromises()
    await clickSub(w, '结构')
    expect(w.findAll('tbody tr')).toHaveLength(1)
    expect(w.text()).not.toContain('加载中…')
    // DDL 页仍处于加载中(各自的标志)
    await clickSub(w, 'DDL')
    expect(w.text()).toContain('加载中…')
    releaseDdl({ ddl: 'CREATE TABLE t;' })
    await flushPromises()
    await clickSub(w, 'DDL')
    expect(w.text()).not.toContain('加载中…')
  })
})
