<script setup lang="ts">
import { json } from '@codemirror/lang-json'
import { sql } from '@codemirror/lang-sql'
import { Compartment, EditorState, Prec } from '@codemirror/state'
import { oneDark } from '@codemirror/theme-one-dark'
import { EditorView, keymap } from '@codemirror/view'
import { basicSetup } from 'codemirror'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { useThemeStore } from '@/stores/theme'

const props = withDefaults(defineProps<{
  modelValue: string
  lang?: 'sql' | 'json'
  placeholder?: string
  readonly?: boolean
}>(), { lang: 'sql', placeholder: '', readonly: false })

const emit = defineEmits<{
  'update:modelValue': [string]
  execute: []
}>()

const host = ref<HTMLElement>()
const theme = useThemeStore()
const themeComp = new Compartment()
let view: EditorView | null = null

function buildTheme() {
  return theme.mode === 'dark' ? oneDark : EditorView.theme({}, { dark: false })
}

onMounted(() => {
  const langExt = props.lang === 'json' ? json() : sql()
  view = new EditorView({
    parent: host.value!,
    state: EditorState.create({
      doc: props.modelValue,
      extensions: [
        basicSetup,
        langExt,
        themeComp.of(buildTheme()),
        EditorView.lineWrapping,
        // 只读(DDL 展示):静态语义,挂载后不再切换,不必进 Compartment
        ...(props.readonly
          ? [EditorState.readOnly.of(true), EditorView.editable.of(false)]
          : []),
        // Prec.highest:basicSetup 自带的 Mod-Enter(插入空行)先注册会先消费按键
        Prec.highest(keymap.of([
          { key: 'Mod-Enter', run: () => { emit('execute'); return true } },
        ])),
        EditorView.updateListener.of(u => {
          if (u.docChanged) emit('update:modelValue', u.state.doc.toString())
        }),
      ],
    }),
  })
})

watch(() => theme.mode, () => {
  view?.dispatch({ effects: themeComp.reconfigure(buildTheme()) })
})

watch(() => props.modelValue, (v) => {
  if (view && v !== view.state.doc.toString()) {
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: v } })
  }
})

onBeforeUnmount(() => { view?.destroy(); view = null })
</script>

<template>
  <div ref="host" class="cm-host" />
</template>
