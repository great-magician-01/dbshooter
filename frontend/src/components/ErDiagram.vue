<script setup lang="ts">
/**
 * ER 图(自绘 SVG,零依赖):当前表为中心,我引用的表在右、引用我的在左。
 * 点击邻居表可切换为中心重新加载;提供缩放与"回到初始表"。
 * 布局/图构建全部在 utils/erLayout.ts(纯函数),本组件只负责取数与渲染。
 */
import { computed, onMounted, ref } from 'vue'

import { get } from '@/api/http'
import AppIcon from '@/components/AppIcon.vue'
import { useConnectionsStore } from '@/stores/connections'
import type { ColumnInfo, TableRelation, Tab } from '@/types'
import { buildGraph, HEADER_H, layoutEr, ROW_H } from '@/utils/erLayout'
import type { ErCol, ErLayout } from '@/utils/erLayout'

const props = defineProps<{ tab: Tab }>()
const conns = useConnectionsStore()

const connType = computed(() =>
  conns.items.find(c => c.id === props.tab.connection_id)?.type ?? 'sqlite')
const isView = computed(() => props.tab.context.kind === 'view')
const initialPath = (props.tab.context.path as string) || ''

/** 节点 key 口径与 buildGraph 的 defaultKeyOf 一致:sqlite 裸表名,其余 schema.table */
function tableKey(schema: string, table: string): string {
  return connType.value === 'sqlite' ? table : `${schema}.${table}`
}

/** 树节点路径 → (schema, table):sqlite main.<t> / mysql <db>.<t> / pg <db>.<schema>.<t> */
function parsePath(path: string): { schema: string; table: string } {
  const parts = path.split('.')
  return { schema: parts[parts.length - 2] ?? 'main', table: parts[parts.length - 1] }
}

/** (schema, table) → 元数据路径(拼给 /structure、/relations) */
function pathOf(schema: string, table: string): string {
  if (connType.value === 'sqlite') return `main.${table}`
  if (connType.value === 'pg') return `${initialPath.split('.')[0]}.${schema}.${table}`
  return `${schema}.${table}`
}

interface Center { key: string; schema: string; table: string; path: string }

function initialCenter(): Center {
  const { schema, table } = parsePath(initialPath)
  return { key: tableKey(schema, table), schema, table, path: initialPath }
}

const center = ref<Center>(initialCenter())
const initial = initialCenter()   // 初始表(“回到初始表”按钮的锚点)
const layout = ref<ErLayout | null>(null)
const overflow = ref({ left: 0, right: 0 })
const loading = ref(true)
const error = ref('')
const scale = ref(1)
/** 结构缓存:中心切换来回时不必重查邻居列(组件存活期内有效) */
const structCache = new Map<string, ErCol[]>()
/** 邻居节点 click 反查(key → schema/table,拼 recenter 路径用),每次 load 重建 */
const neighborMeta = ref(new Map<string, { schema: string; table: string }>())
let gen = 0   // 代次守卫:快速连点邻居时丢弃过期响应(照 SqlPane runGen 模式)

async function fetchColumns(path: string, key: string): Promise<ErCol[]> {
  const hit = structCache.get(key)
  if (hit) return hit
  const cid = props.tab.connection_id
  const r = await get<{ columns: ColumnInfo[] }>(`/api/connections/${cid}/structure`, { path })
  const cols = r.columns.map(c => ({ name: c.name, pk: c.pk > 0 }))
  structCache.set(key, cols)
  return cols
}

async function load() {
  const g = ++gen
  const cid = props.tab.connection_id
  if (!cid) { error.value = '页签未绑定连接'; loading.value = false; return }
  loading.value = true
  error.value = ''
  try {
    const c = center.value
    const [cols, relResp] = await Promise.all([
      fetchColumns(c.path, c.key),
      get<{ relations: TableRelation[] }>(`/api/connections/${cid}/relations`,
        { path: c.path }),
    ])
    const relations = relResp.relations
    // 邻居 key → (schema, table),供拼结构路径与点击居中
    const neighbors = new Map<string, { schema: string; table: string }>()
    for (const r of relations) {
      const [ns, nt] = r.direction === 'out' ? [r.ref_schema, r.ref_table] : [r.schema, r.table]
      const k = tableKey(ns, nt)
      if (k !== c.key) neighbors.set(k, { schema: ns, table: nt })
    }
    // 邻居列并发拉取(带缓存);单个失败不拖垮全图,该邻居画空列卡片
    await Promise.all([...neighbors.entries()].map(([k, v]) =>
      fetchColumns(pathOf(v.schema, v.table), k).catch(() => [])))
    if (g !== gen) return
    const gph = buildGraph(c.key, c.table, cols, relations,
      k => structCache.get(k), tableKey)
    layout.value = layoutEr(gph.nodes, gph.edges)
    overflow.value = gph.overflow
    neighborMeta.value = neighbors
  } catch (e: any) {
    if (g !== gen) return
    error.value = e.message
    layout.value = null
  } finally {
    if (g === gen) loading.value = false
  }
}

/** 点击邻居:切换为中心重新加载 */
function onNodeClick(key: string, side: string) {
  if (side === 'center') return
  const meta = neighborMeta.value.get(key)
  if (meta) {
    center.value = { key, schema: meta.schema, table: meta.table,
                     path: pathOf(meta.schema, meta.table) }
    load()
  }
}

function backToInitial() {
  center.value = initialCenter()
  load()
}

function zoom(delta: number) {
  scale.value = Math.min(1.5, Math.max(0.5, Math.round((scale.value + delta) * 10) / 10))
}

onMounted(load)
</script>

<template>
  <section class="pane">
    <div class="er-toolbar">
      <button class="pt-btn" title="放大" @click="zoom(0.1)"><AppIcon name="plus" :size="11" /></button>
      <button class="pt-btn" title="复位缩放" @click="scale = 1">{{ Math.round(scale * 100) }}%</button>
      <button class="pt-btn" title="缩小" @click="zoom(-0.1)">−</button>
      <button v-if="center.key !== initial.key" class="pt-btn" @click="backToInitial">
        回到 {{ initial.table }}</button>
      <span v-if="overflow.left || overflow.right" class="result-hint">
        另有 {{ overflow.left + overflow.right }} 张关联表未展示</span>
      <span class="result-status">{{ center.table }}</span>
    </div>
    <div class="result-body">
      <div v-if="error" class="result-placeholder" style="color:var(--red)">{{ error }}</div>
      <div v-else-if="loading" class="result-placeholder">加载中…</div>
      <div v-else-if="!layout || layout.edges.length === 0"
           class="result-placeholder">
        {{ isView ? '视图没有外键关系' : '该表没有外键关系' }}
      </div>
      <svg v-else :width="layout.width * scale" :height="layout.height * scale">
        <g :transform="`scale(${scale})`">
          <!-- 边:FK 侧 → 被引侧;悬停看约束名 -->
          <path v-for="(e, i) in layout.edges" :key="`e${i}`" :d="e.d" class="er-edge">
            <title>{{ e.name }}</title>
          </path>
          <text v-for="(e, i) in layout.edges" :key="`el${i}`" :x="e.lx" :y="e.ly"
                class="er-edge-label">{{ e.name }}</text>
          <!-- 节点卡片 -->
          <g v-for="n in layout.nodes" :key="n.key" class="er-node"
             :class="{ center: n.side === 'center', clickable: n.side !== 'center' }"
             @click="onNodeClick(n.key, n.side)">
            <rect :x="n.x" :y="n.y" :width="n.w" :height="n.h" rx="6" class="er-card" />
            <rect :x="n.x" :y="n.y" :width="n.w" :height="HEADER_H" rx="6" class="er-header" />
            <!-- 遮住表头圆角的下半,形成上圆下方的头部 -->
            <rect :x="n.x" :y="n.y + HEADER_H - 6" :width="n.w" height="6" class="er-header" />
            <text :x="n.x + 10" :y="n.y + 18" class="er-title">{{ n.label }}</text>
            <g v-for="(c, i) in n.columns" :key="c.name">
              <!-- 主键小钥匙(12px,icons.ts 同款 key 图形) -->
              <g v-if="c.pk"
                 :transform="`translate(${n.x + 8}, ${n.y + HEADER_H + ROW_H * i + 5}) scale(0.75)`"
                 class="er-pk-ico">
                <circle cx="5.2" cy="5.2" r="2.6" />
                <path d="M7.1 7.1L13 13M10.7 9.7l1.8-1.8M12.3 11.3l1.6-1.6" />
              </g>
              <text :x="c.pk ? n.x + 24 : n.x + 10"
                    :y="n.y + HEADER_H + ROW_H * i + 15"
                    class="er-col" :class="{ pk: c.pk }">{{ c.name }}</text>
            </g>
          </g>
        </g>
      </svg>
    </div>
  </section>
</template>
