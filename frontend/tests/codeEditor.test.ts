/** 组件级:编辑器快捷键 —— Ctrl/Cmd+Enter 触发执行,而不是 basicSetup 默认的"插入空行"。 */
import { enableAutoUnmount, flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest'

import CodeEditor from '@/components/CodeEditor.vue'

// jsdom 未实现 Range.getClientRects,CodeMirror 测距时会调用
beforeAll(() => {
  Range.prototype.getClientRects = () => ({ length: 0, item: () => null, [Symbol.iterator]: [][Symbol.iterator] }) as any
  Range.prototype.getBoundingClientRect = () => ({ x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, toJSON: () => ({}) }) as any
})
afterAll(() => {
  delete (Range.prototype as any).getClientRects
  delete (Range.prototype as any).getBoundingClientRect
})

// 卸载编辑器,避免 CodeMirror 的测量循环残留到下一个用例
enableAutoUnmount(afterEach)

function pressEnter(w: VueWrapper, mods: KeyboardEventInit) {
  w.find('.cm-content').element.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, ...mods }))
}

describe('CodeEditor', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('Ctrl+Enter 触发 execute,且不插入空行', () => {
    const w = mount(CodeEditor, { props: { modelValue: 'SELECT 1' } })
    expect(w.emitted('execute')).toBeUndefined()
    pressEnter(w, { ctrlKey: true })
    expect(w.emitted('execute')).toHaveLength(1)
    // 未被 Mod-Enter 的 insertBlankLine 改动
    expect(w.emitted('update:modelValue')).toBeUndefined()
  })

  it('单独 Enter 仍是换行,不触发执行', () => {
    const w = mount(CodeEditor, { props: { modelValue: 'SELECT 1' } })
    pressEnter(w, {})
    expect(w.emitted('execute')).toBeUndefined()
  })

  it('readonly:编辑器不可编辑,但外部 modelValue 变化仍能写入', async () => {
    const w = mount(CodeEditor, { props: { modelValue: 'CREATE TABLE t (a int)', readonly: true } })
    // 走 DOM 侧断言:EditorView.editable.of(false) → contenteditable=false
    const content = w.find('.cm-content').element as HTMLElement
    expect(content.getAttribute('contenteditable')).toBe('false')
    // 只读态下 typing 不产生任何改动
    pressEnter(w, {})
    expect(w.emitted('execute')).toBeUndefined()
    // 外部换文档仍生效(DDL 异步到账后要能显示)
    await w.setProps({ modelValue: 'CREATE TABLE t2 (b int)' })
    await flushPromises()
    expect(w.find('.cm-content').element.textContent).toContain('CREATE TABLE t2')
  })

  it('非 readonly:默认 contenteditable=true', () => {
    const w = mount(CodeEditor, { props: { modelValue: 'SELECT 1' } })
    expect(w.find('.cm-content').element.getAttribute('contenteditable')).toBe('true')
  })
})
