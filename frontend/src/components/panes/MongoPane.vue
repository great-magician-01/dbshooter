<script setup lang="ts">
import { ref } from 'vue'

import { post } from '@/api/http'
import CodeEditor from '@/components/CodeEditor.vue'
import type { Tab } from '@/types'

const props = defineProps<{ tab: Tab }>()

const query = ref(props.tab.content || 'db.test.find({}).limit(50)')
const docs = ref<any[]>([])
const status = ref('就绪 · Ctrl+Enter 执行')
const running = ref(false)

async function run() {
  if (running.value) return
  running.value = true
  status.value = '查询中…'
  try {
    const { results } = await post<{ results: any[] }>('/api/query/execute',
      { conn_id: props.tab.connection_id, stmt: query.value, limit: 200 })
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
        {{ running ? '查询中…' : '▶ 执行' }} <span style="opacity:.6;font-size:11px">Ctrl+Enter</span>
      </button>
      <div class="pt-conn">集合 <b style="color:var(--text);font-family:var(--mono)">{{ tab.context?.collection ?? '(未选)' }}</b>
        <span style="color:var(--muted)">· 表格视图二期提供</span>
      </div>
    </div>
    <div class="mongo-editor">
      <CodeEditor lang="json" :model-value="query" @update:model-value="query = $event" @execute="run" />
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
