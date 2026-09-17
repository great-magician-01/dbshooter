<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'

import { get, post } from '@/api/http'
import { ws } from '@/api/ws'
import AppIcon from '@/components/AppIcon.vue'
import CodeEditor from '@/components/CodeEditor.vue'
import { iconSvg } from '@/components/icons'
import ResultGrid from '@/components/ResultGrid.vue'
import { useConnectionsStore } from '@/stores/connections'
import { useUiStore } from '@/stores/ui'
import { useWorkspaceStore } from '@/stores/workspace'
import type { Column, QueryDone, Tab } from '@/types'
import { formatMs, formatSql } from '@/utils/format'
import { useSplitter } from '@/utils/split'

const props = defineProps<{ tab: Tab }>()

const conns = useConnectionsStore()
const workspace = useWorkspaceStore()
const ui = useUiStore()

const running = ref(false)
const columns = ref<Column[]>([])
const rows = ref<any[][]>([])
const hasMore = ref(false)
const truncated = ref(false)
const statusText = ref('就绪 · Ctrl+Enter 执行')
const view = ref<'grid' | 'plan' | 'log'>('grid')
const planText = ref('')
const logLines = ref<string[]>([])
const queryId = ref<string | null>(null)
const showConnSelect = ref(false)
const loadingMore = ref(false)
/** 执行代次:run / cancel 各自自增,事件回调凭捕获的代次丢弃过期事件 */
let runGen = 0

// 编辑器 / 结果区高度:分隔条可拖拽(见 .hsplit),上限随面板实际高度收
const paneEl = ref<HTMLElement>()
const { size: resultsHeight, onPointerDown: resultsSplit } = useSplitter(280, {
  axis: 'y', side: 'end', min: 110,
  max: () => Math.max(160, (paneEl.value?.clientHeight ?? 640) - 120),
  storageKey: 'ds-results-h',
})

const sqlConns = computed(() => conns.items.filter(c => ['sqlite', 'mysql', 'pg'].includes(c.type)))

/** query.started / query.rows / query.done / query.error(含服务端兜底 error)的事件数据合集 */
type QueryEventData = Partial<QueryDone> & {
  columns?: Column[]
  rows?: any[][]
  has_more?: boolean
  error?: string
  message?: string
}

/** GET /api/query/{qid}/rows 的分页响应(has_more 只表示缓冲区内还有未拉取的行) */
type RowsPage = {
  rows: any[][]
  has_more: boolean
  truncated?: boolean
  total_buffered: number
}

/** 页签绑定的 schema(右键 PG schema/表 新建的标签页):查询免写 schema 前缀 */
const schema = computed(() => (props.tab.context?.schema as string | undefined) ?? null)

const connId = computed({
  // 不回退到第一个连接:绑定的连接被删后宁可不执行,也不要跑到别的库上
  get: () => props.tab.connection_id ?? '',
  set: (v: string) => {
    // 换连接后原 schema 绑定对新连接无意义,解绑
    if (props.tab.connection_id !== v && props.tab.context?.schema)
      delete props.tab.context.schema
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
  if (!connId.value) { statusText.value = '请先选择连接'; pushLog('请先选择连接', 'err'); return }
  const gen = ++runGen   // 本次执行的代次:取消 / 重跑都会让在途事件作废
  running.value = true
  statusText.value = '执行中…'
  // 清空上一次查询的状态:非行结果集(UPDATE/INSERT/DDL)不会再有 query.rows 事件
  queryId.value = null
  columns.value = []
  rows.value = []
  hasMore.value = false
  truncated.value = false
  loadingMore.value = false
  pushLog(escapeHtml(stmt.split('\n').find(l => l.trim()) ?? stmt))
  await ws.send('query.execute',
    { conn_id: connId.value, stmt, schema: schema.value ?? undefined }, (ev) => {
    // 取消后立刻重跑时,旧执行的事件可能还在路上:代次不符一律丢弃,
    // 否则旧 query.done 会把新执行的状态改成"已完成"、running 被提前复位
    if (gen !== runGen) return
    const d = (ev.data ?? {}) as QueryEventData
    if (ev.event === 'query.started') {
      queryId.value = d.query_id ?? null
    } else if (ev.event === 'query.rows') {
      columns.value = d.columns ?? []; rows.value = d.rows ?? []
      hasMore.value = d.has_more ?? false
      truncated.value = d.truncated ?? false
      view.value = 'grid'
    } else if (ev.event === 'query.done') {
      const rc = d.row_count ?? 0
      const ms = formatMs(d.elapsed_ms)
      // 纯写语句后端不发 query.rows,受影响行数只能从 summary 里汇总
      const kinds = (d.summary ?? []).map(s => s.kind)
      const affected = (d.summary ?? []).reduce((n, s) => n + (s.affected ?? 0), 0)
      const label = kinds.includes('rows') || kinds.includes('documents')
        ? `${rc} 行`
        : kinds.includes('affected') ? `${affected} 行受影响` : `${rc} 行`
      // 注意:query.done 不带 truncated(截断信息只在 query.rows 首包),别在这里读它
      statusText.value = `${label} · ${ms}`
      workspace.lastRun = { rows: rc, ms: d.elapsed_ms ?? 0 }
      pushLog(`${iconSvg('check')} ${label},${ms}`, 'ok')
      running.value = false
      ws.done(ev.id)
    } else if (ev.event === 'query.error' || ev.event === 'error') {
      // error:连接断开等服务端兜底事件,与 query.error 同样收尾
      const msg = d.error ?? d.message ?? '执行失败'
      statusText.value = `错误:${msg}`
      pushLog(`${iconSvg('close')} ${escapeHtml(msg)}`, 'err')
      running.value = false
      ws.done(ev.id)
    }
  }).catch(e => {
    if (gen !== runGen) return
    statusText.value = e.message; running.value = false
  })
}

async function showPlan() {
  const stmt = props.tab.content.trim()
  if (!stmt) return
  view.value = 'plan'
  if (!connId.value) { planText.value = '请先选择连接'; return }
  planText.value = '加载中…'
  try {
    const first = stmt.split(';').map(s => s.trim()).filter(Boolean)[0]
    // EXPLAIN QUERY PLAN 是 SQLite 专有语法,MySQL / PG 都用 EXPLAIN
    const dialect = conns.items.find(c => c.id === connId.value)?.type === 'sqlite'
      ? `EXPLAIN QUERY PLAN ${first}` : `EXPLAIN ${first}`
    const { results } = await post<{ results: any[] }>('/api/query/execute',
      { conn_id: connId.value, stmt: dialect, limit: 100, schema: schema.value ?? undefined })
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
  runGen++   // 在途事件作废:取消后旧事件不得再改状态
  if (queryId.value) await post('/api/query/cancel', { query_id: queryId.value }).catch(() => {})
  running.value = false
  // 终态事件已被代次守卫丢弃,状态得自己收尾,否则一直停在「执行中…」
  statusText.value = '已取消'
}

/** 大结果集:从服务端缓冲拉下一页(由 ResultGrid 滚动到底时触发,也可点按钮) */
async function loadMore() {
  if (!queryId.value || !hasMore.value || loadingMore.value) return
  const qid = queryId.value    // 请求在途时可能重新执行,回来要认的是同一个 qid
  loadingMore.value = true
  try {
    const d = await get<RowsPage>(`/api/query/${qid}/rows`,
      { offset: rows.value.length, limit: 500 })
    if (queryId.value !== qid) return    // 已换/已重发查询,丢弃这一页
    rows.value.push(...d.rows)
    hasMore.value = d.has_more
    truncated.value = d.truncated ?? false
    const buffered = `${rows.value.length} 行(已缓冲 ${d.total_buffered})`
    statusText.value = d.has_more
      ? `${buffered} · 还有更多`
      : truncated.value ? `${buffered} · 已达 2000 行缓冲上限,请加 LIMIT 缩小结果` : buffered
  } finally {
    loadingMore.value = false
  }
}

function escapeHtml(s: string) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;')
}

// 顶栏"执行"按钮
watch(() => ui.executeNonce, () => {
  if (workspace.activeId === props.tab.id) run()
})

// AI"插入并执行":本页签被指定时执行一次。
// 用 watch + immediate 而非 onMounted:KeepAlive 缓存的组件重新激活不再触发 onMounted。
watch(() => ui.pendingRunTabId, (id) => {
  if (id !== props.tab.id) return
  ui.pendingRunTabId = null
  nextTick(() => run())
}, { immediate: true })

defineExpose({ run })
</script>

<template>
  <section class="pane" ref="paneEl">
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
      <span v-if="schema" class="pt-schema"
            title="本页签已绑定 schema:SQL 无需写 schema 前缀(后端注入 search_path)">
        <AppIcon name="database" :size="11" /> {{ schema }}
      </span>
    </div>
    <div class="editor-wrap">
      <CodeEditor lang="sql" :model-value="tab.content" @update:model-value="onEdit" @execute="run" />
    </div>
    <div class="hsplit" @pointerdown="resultsSplit" />
    <div class="results" :style="{ height: resultsHeight + 'px' }">
      <div class="result-tabs">
        <span class="rt-tab" :class="{ active: view === 'grid' }" @click="view = 'grid'">
          结果 <span v-if="rows.length" class="rt-badge">{{ rows.length }}</span>
        </span>
        <span class="rt-tab" :class="{ active: view === 'plan' }" @click="view = 'plan'">执行计划</span>
        <span class="rt-tab" :class="{ active: view === 'log' }" @click="view = 'log'">日志</span>
        <span class="result-status">{{ statusText }}</span>
        <!-- has_more = 缓冲区内还有未拉取的行(与是否被截断无关),有就继续能拉 -->
        <button v-if="hasMore && view === 'grid'" class="pt-btn" style="margin-left:8px"
                :disabled="loadingMore" @click="loadMore">
          {{ loadingMore ? '加载中…' : '加载更多' }}
        </button>
        <!-- 缓冲已拉完且被驱动层 2000 行上限截断:再拉也没有了,只能靠 LIMIT 缩小结果 -->
        <span v-else-if="view === 'grid' && truncated" class="result-hint">
          已达 2000 行缓冲上限,请加 LIMIT 缩小结果
        </span>
      </div>
      <div class="result-body">
        <template v-if="view === 'grid'">
          <ResultGrid v-if="rows.length" :columns="columns" :rows="rows" @reach-end="loadMore" />
          <div v-else class="result-placeholder">执行 SQL 后在此展示结果集</div>
        </template>
        <pre v-else-if="view === 'plan'" class="plan">{{ planText }}</pre>
        <pre v-else class="log" v-html="logLines.join('\n') || '<span style=&quot;color:var(--muted)&quot;>(暂无日志)</span>'" />
      </div>
    </div>
  </section>
</template>
