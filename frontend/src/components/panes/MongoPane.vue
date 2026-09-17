<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { post } from '@/api/http'
import AppIcon from '@/components/AppIcon.vue'
import CodeEditor from '@/components/CodeEditor.vue'
import { useWorkspaceStore } from '@/stores/workspace'
import type { Tab } from '@/types'

const props = defineProps<{ tab: Tab }>()

const workspace = useWorkspaceStore()
const docs = ref<any[]>([])
const status = ref('就绪 · Ctrl+Enter 执行')
const running = ref(false)

// 内容存 workspace 的页签里(与 SqlPane 一致):切换页签/重启后不丢
onMounted(() => {
  if (!props.tab.content)
    workspace.setContent(props.tab.id,
      props.tab.context?.collection
        ? `db.${props.tab.context.collection}.find({}).sort({ _id: -1 }).limit(50)`
        : 'db.test.find({}).limit(50)')
})

function onEdit(v: string) {
  workspace.setContent(props.tab.id, v)
}

async function run() {
  if (running.value) return
  running.value = true
  status.value = '查询中…'
  try {
    const { results } = await post<{ results: any[] }>('/api/query/execute',
      { conn_id: props.tab.connection_id, stmt: props.tab.content, limit: 200 })
    const r = results[0]
    if (r.error) { status.value = `错误:${r.error}`; docs.value = [] }
    else if (r.kind === 'documents') {
      docs.value = r.raw ?? []
      status.value = `${docs.value.length} 文档 · ${r.elapsed_ms} ms${r.truncated ? ' · 已截断' : ''}`
    } else {
      docs.value = []
      status.value = r.kind === 'affected' ? `影响 ${r.affected} 条 · ${r.elapsed_ms} ms`
        : `结果:${JSON.stringify(r.raw)} · ${r.elapsed_ms} ms`
    }
  } catch (e: any) {
    status.value = e.message
  } finally {
    running.value = false
  }
}
</script>

<template>
  <section class="pane">
    <div class="pane-toolbar">
      <button class="pt-btn run" :disabled="running" @click="run">
        <AppIcon v-if="!running" name="play" :size="10" /> {{ running ? '查询中…' : '执行' }} <span style="opacity:.6;font-size:11px">Ctrl+Enter</span>
      </button>
      <div class="pt-conn">集合 <b style="color:var(--text);font-family:var(--mono)">{{ tab.context?.collection ?? '(未选)' }}</b>
        <span style="color:var(--muted)">· 表格视图二期提供</span>
      </div>
    </div>
    <div class="mongo-editor">
      <CodeEditor lang="json" :model-value="tab.content" @update:model-value="onEdit" @execute="run" />
    </div>
    <div class="result-tabs">
      <span class="rt-tab active">文档 <span class="rt-badge">{{ docs.length }}</span></span>
      <span class="result-status">{{ status }}</span>
    </div>
    <div class="mdocs">
      <div v-for="(doc, i) in docs" :key="i" class="mdoc">
        <div class="mdoc-head">#{{ i + 1 }} · _id: {{ doc._id?.$oid ?? doc._id }}</div>
        <pre>{{ JSON.stringify(doc, null, 2) }}</pre>
      </div>
      <div v-if="!docs.length" class="result-placeholder">执行查询后在此展示文档</div>
    </div>
  </section>
</template>
