<script setup lang="ts">
import { json } from '@codemirror/lang-json'
import { sql } from '@codemirror/lang-sql'
import { Compartment, EditorState } from '@codemirror/state'
import { oneDark } from '@codemirror/theme-one-dark'
import { EditorView, keymap } from '@codemirror/view'
import { basicSetup } from 'codemirror'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { useThemeStore } from '@/stores/theme'

const props = withDefaults(defineProps<{
  modelValue: string
  lang?: 'sql' | 'json'
  placeholder?: string
}>(), { lang: 'sql', placeholder: '' })

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
        keymap.of([{ key: 'Ctrl-Enter', run: () => { emit('execute'); return true } }]),
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

function insertAtEnd(text: string) {
  if (!view) return
  const len = view.state.doc.length
  const sep = view.state.doc.length ? '\n\n' : ''
  view.dispatch({ changes: { from: len, insert: sep + text + '\n' } })
  view.focus()
}

function focus() { view?.focus() }

defineExpose({ insertAtEnd, focus })

onBeforeUnmount(() => { view?.destroy(); view = null })
</script>

<template>
  <div ref="host" class="cm-host" />
</template>
