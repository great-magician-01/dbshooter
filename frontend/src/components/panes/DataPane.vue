<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { http, post } from '@/api/http'
import AppIcon from '@/components/AppIcon.vue'
import ResultGrid from '@/components/ResultGrid.vue'
import type { Column, Tab } from '@/types'

const props = defineProps<{ tab: Tab }>()

const columns = ref<Column[]>([])
const rows = ref<any[][]>([])
const filter = ref('')
const status = ref('加载中…')
const error = ref('')

const table = (props.tab.context.table as string) ?? ''
// 带 schema 与引号的限定引用(打开页签时按驱动类型生成);旧持久化页签无此字段,回退裸表名
const tableRef = (props.tab.context.ref as string) || table
const stmt = () =>
  `SELECT * FROM ${tableRef}${filter.value.trim() ? ` WHERE ${filter.value.trim()}` : ''} LIMIT 500`

async function load() {
  error.value = ''
  status.value = '查询中…'
  try {
    const { results } = await post<{ results: any[] }>('/api/query/execute',
      { conn_id: props.tab.connection_id, stmt: stmt(), limit: 500 })
    const r = results[0]
    if (r.error) { error.value = r.error; rows.value = []; status.value = '查询失败' }
    else {
      columns.value = r.columns; rows.value = r.rows
      status.value = `${r.rows.length} 行${r.truncated ? ' · 截断于 500' : ''} · ${r.elapsed_ms} ms`
    }
  } catch (e: any) {
    error.value = e.message; status.value = '查询失败'
  }
}

async function exportCsv() {
  try {
    const resp = await http.post('/api/query/export',
      { conn_id: props.tab.connection_id, stmt: stmt() }, { responseType: 'blob' })
    const url = URL.createObjectURL(resp.data)
    const a = document.createElement('a')
    a.href = url; a.download = `${table}.csv`; a.click()
    URL.revokeObjectURL(url)
  } catch (e: any) {
    error.value = e.message
  }
}

onMounted(load)
</script>

<template>
  <section class="pane">
    <div class="pane-toolbar">
      <span class="tn-ico"><AppIcon name="table" /></span>
      <b style="font-size:12.5px">{{ tab.title }}</b>
      <button class="pt-btn" @click="load">刷新</button>
      <button class="pt-btn" title="二期:行编辑/新增" disabled><AppIcon name="plus" :size="11" /> 新增行</button>
      <button class="pt-btn" @click="exportCsv">导出 CSV</button>
      <div class="pt-conn">
        <input v-model="filter" placeholder="过滤 (WHERE 条件,回车生效)" style="width:240px"
               @keyup.enter="load">
      </div>
    </div>
    <div class="result-body" style="flex:1">
      <div v-if="error" class="result-placeholder" style="color:var(--red)">{{ error }}</div>
      <ResultGrid v-else-if="rows.length" :columns="columns" :rows="rows" />
      <div v-else class="result-placeholder">{{ status }}</div>
    </div>
    <div class="result-tabs" style="border-top:1px solid var(--border-soft);border-bottom:0">
      <span class="result-status" style="margin-left:0">{{ status }}</span>
    </div>
  </section>
</template>
