<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'

import { get, post } from '@/api/http'
import { ws } from '@/api/ws'
import AppIcon from '@/components/AppIcon.vue'
import CodeEditor from '@/components/CodeEditor.vue'
import { iconSvg } from '@/components/icons'
import ResultGrid from '@/components/ResultGrid.vue'
import { useConnectionsStore } from '@/stores/connections'
import { useUiStore } from '@/stores/ui'
import { useWorkspaceStore } from '@/stores/workspace'
import type { Column, Tab } from '@/types'
import { formatMs, formatSql } from '@/utils/format'

const props = defineProps<{ tab: Tab }>()

const conns = useConnectionsStore()
const workspace = useWorkspaceStore()
const ui = useUiStore()

const editorRef = ref<InstanceType<typeof CodeEditor>>()
const running = ref(false)
const columns = ref<Column[]>([])
const rows = ref<any[][]>([])
const hasMore = ref(false)
const statusText = ref('就绪 · Ctrl+Enter 执行')
const view = ref<'grid' | 'plan' | 'log'>('grid')
const planText = ref('')
const logLines = ref<string[]>([])
const queryId = ref<string | null>(null)
const showConnSelect = ref(false)

const sqlConns = computed(() => conns.items.filter(c => ['sqlite', 'mysql', 'pg'].includes(c.type)))
const connId = computed({
  get: () => props.tab.connection_id ?? sqlConns.value[0]?.id ?? '',
  set: (v: string) => {
    props.tab.connection_id = v
    workspace.setContent(props.tab.id, props.tab.content)   // 触发一次保存
    persist()
  },
})

function persist() {
  post('/api/workspace/tabs/save', {
    id: props.tab.id, type: props.tab.type, title: props.tab.title,
    connection_id: props.tab.connection_id, context: props.tab.context,
    content: props.tab.content, sort: props.tab.sort,
  }).catch(() => {})
}

function onEdit(v: string) {
  workspace.setContent(props.tab.id, v)
}

function pushLog(line: string, cls = '') {
  const time = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  logLines.value.push(`<span class="${cls}">[${time}] ${line}</span>`)
  if (logLines.value.length > 200) logLines.value.shift()
}

async function run() {
  const stmt = props.tab.content.trim()
  if (!stmt || running.value) return
  if (!connId.value) { statusText.value = '请先选择连接'; return }
  running.value = true
  statusText.value = '执行中…'
  pushLog(escapeHtml(stmt.split('\n').find(l => l.trim()) ?? stmt))
  await ws.send('query.execute', { conn_id: connId.value, stmt }, (ev) => {
    const d = ev.data ?? {}
    if (ev.event === 'query.started') {
      queryId.value = d.query_id
    } else if (ev.event === 'query.rows') {
      columns.value = d.columns; rows.value = d.rows; hasMore.value = d.has_more
      view.value = 'grid'
    } else if (ev.event === 'query.done') {
      const rc = d.row_count ?? 0
      statusText.value = `${rc} 行 · ${formatMs(d.elapsed_ms)}${d.truncated ? ' · 已截断' : ''}`
      workspace.lastRun = { rows: rc, ms: d.elapsed_ms }
      pushLog(`${iconSvg('check')} ${rc} 行,${formatMs(d.elapsed_ms)}`, 'ok')
      running.value = false
      ws.done(ev.id)
    } else if (ev.event === 'query.error') {
      statusText.value = `错误:${d.error ?? '执行失败'}`
      pushLog(`${iconSvg('close')} ${d.error ?? '执行失败'}`, 'err')
      running.value = false
      ws.done(ev.id)
    }
  }).catch(e => { statusText.value = e.message; running.value = false })
}

async function showPlan() {
  const stmt = props.tab.content.trim()
  if (!stmt || !connId.value) return
  view.value = 'plan'
  planText.value = '加载中…'
  try {
    const first = stmt.split(';').map(s => s.trim()).filter(Boolean)[0]
    const dialect = conns.items.find(c => c.id === connId.value)?.type === 'mysql'
      ? `EXPLAIN ${first}` : `EXPLAIN QUERY PLAN ${first}`
    const { results } = await post<{ results: any[] }>('/api/query/execute',
      { conn_id: connId.value, stmt: dialect, limit: 100 })
    const r = results[0]
    planText.value = r?.rows?.length
      ? r.rows.map((row: any[]) => row.join('  |  ')).join('\n')
      : (r?.error ?? '无计划输出(该语句可能不支持 EXPLAIN)')
  } catch (e: any) {
    planText.value = e.message
  }
}

function format() {
  onEdit(formatSql(props.tab.content))
}

async function cancel() {
  if (queryId.value) await post('/api/query/cancel', { query_id: queryId.value }).catch(() => {})
  running.value = false
}

/** 大结果集:从服务端缓冲拉下一页 */
async function loadMore() {
  if (!queryId.value) return
  const d = await get<any>(`/api/query/${queryId.value}/rows`,
    { offset: rows.value.length, limit: 500 })
  rows.value.push(...d.rows)
  hasMore.value = d.has_more
  statusText.value = `${rows.value.length} 行(已缓冲 ${d.total_buffered})${d.has_more ? ' · 还有更多' : ''}`
}

function escapeHtml(s: string) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;')
}

// 顶栏"执行"按钮
watch(() => ui.executeNonce, () => {
  if (workspace.activeId === props.tab.id) run()
})

// AI"插入并执行":本 Tab 挂载后自动执行
onMounted(() => {
  if (ui.pendingRunTabId === props.tab.id) {
    ui.pendingRunTabId = null
    nextTick(() => run())
  }
})

defineExpose({ run })
</script>

<template>
  <section class="pane">
    <div class="pane-toolbar">
      <button class="pt-btn run" :disabled="running" @click="run">
        <AppIcon v-if="!running" name="play" :size="10" /> {{ running ? '执行中…' : '执行' }} <span style="opacity:.6;font-size:11px">Ctrl+Enter</span>
      </button>
      <button v-if="running" class="pt-btn" @click="cancel">取消</button>
      <button class="pt-btn" @click="showPlan">执行计划</button>
      <button class="pt-btn" @click="format">格式化</button>
      <div class="pt-conn">连接
        <select v-model="connId" @focus="showConnSelect = true">
          <option v-for="c in sqlConns" :key="c.id" :value="c.id">{{ c.name }} / {{ c.database || c.params?.path || c.type }}</option>
        </select>
      </div>
    </div>
    <div class="editor-wrap">
      <CodeEditor ref="editorRef" lang="sql" :model-value="tab.content" @update:model-value="onEdit" @execute="run" />
    </div>
    <div class="hsplit" />
    <div class="results">
      <div class="result-tabs">
        <span class="rt-tab" :class="{ active: view === 'grid' }" @click="view = 'grid'">
          结果 <span v-if="rows.length" class="rt-badge">{{ rows.length }}</span>
        </span>
        <span class="rt-tab" :class="{ active: view === 'plan' }" @click="view = 'plan'">执行计划</span>
        <span class="rt-tab" :class="{ active: view === 'log' }" @click="view = 'log'">日志</span>
        <span class="result-status">{{ statusText }}</span>
        <button v-if="hasMore && view === 'grid'" class="pt-btn" style="margin-left:8px"
                @click="loadMore">加载更多</button>
      </div>
      <div class="result-body">
        <template v-if="view === 'grid'">
          <ResultGrid v-if="rows.length" :columns="columns" :rows="rows" />
          <div v-else class="result-placeholder">执行 SQL 后在此展示结果集</div>
        </template>
        <pre v-else-if="view === 'plan'" class="plan">{{ planText }}</pre>
        <pre v-else class="log" v-html="logLines.join('\n') || '<span style=&quot;color:var(--muted)&quot;>(暂无日志)</span>'" />
      </div>
    </div>
  </section>
</template>
