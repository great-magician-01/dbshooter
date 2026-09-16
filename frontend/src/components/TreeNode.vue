<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import AppIcon from '@/components/AppIcon.vue'
import { useConnectionsStore } from '@/stores/connections'
import { useWorkspaceStore } from '@/stores/workspace'
import type { Connection, MetaNode } from '@/types'
import { qualifiedTable } from '@/utils/ident'

const props = defineProps<{
  conn: Connection
  node: MetaNode          // label 为空串 = 虚拟根(连接本身),直接渲染子级
  depth: number
}>()

const conns = useConnectionsStore()
const workspace = useWorkspaceStore()

const isVirtual = computed(() => props.node.label === '')
const open = ref(isVirtual.value || props.depth === 0)
const loading = ref(false)
const children = ref<MetaNode[] | null>(null)
const sel = ref(false)

const ico = computed(() => {
  switch (props.node.kind) {
    case 'table': return 'table'
    case 'view': return 'view'
    case 'column': return props.node.extra?.pk ? 'pk' : 'column'
    case 'key': return 'key'
    case 'keygroup': return 'keygroup'
    case 'collection': return 'collection'
    case 'index': return 'index'
    case 'database': case 'schema': return 'database'
    default: return 'dot'
  }
})

async function loadChildren() {
  loading.value = true
  try {
    children.value = await conns.metadata(props.conn.id, props.node.path)
  } catch {
    children.value = []
  } finally {
    loading.value = false
  }
}

async function toggle() {
  sel.value = true
  if (!props.node.has_children) return
  open.value = !open.value
  if (open.value && children.value === null) await loadChildren()
}

onMounted(() => { if (isVirtual.value) loadChildren() })

/** 双击叶子节点:按类型打开对应工作台 */
function openTab() {
  const { conn, node } = props
  if (node.kind === 'table' || node.kind === 'view') {
    const segs = node.path.split('.')
    workspace.addTab({
      type: 'data', connection_id: conn.id,
      // PG 等带 schema 段的路径,标题展示 schema 前缀以区分同名表
      title: segs.length >= 3 ? `${segs[segs.length - 2]}.${node.label}` : node.label,
      context: { table: node.label, ref: qualifiedTable(conn.type, node.path), path: node.path },
    })
  } else if (node.kind === 'collection') {
    workspace.addTab({
      type: 'mongo', title: node.label, connection_id: conn.id,
      context: { collection: node.label },
      content: `db.${node.label}.find({}).sort({ _id: -1 }).limit(50)`,
    })
  } else if (node.kind === 'key' || node.kind === 'keygroup') {
    workspace.addTab({
      type: 'redis', title: '键浏览', connection_id: conn.id,
      context: { key: node.extra?.key, db: node.path.split('/')[0] },
    })
  }
}
</script>

<template>
  <div class="tn" :class="{ open, leaf: !node.has_children }">
    <div v-if="!isVirtual" class="tn-row" :class="{ sel }" @click="toggle" @dblclick="openTab">
      <span class="tn-arrow"><AppIcon name="caret" :size="10" /></span>
      <span class="tn-ico" :class="{ pk: ico === 'pk' }"><AppIcon :name="ico" /></span>
      <span class="tn-label" :title="node.label">{{ node.label }}</span>
      <span v-if="loading" class="tn-meta">…</span>
      <span v-else-if="node.kind === 'column' && node.extra?.type" class="tn-meta">{{ node.extra.type }}</span>
    </div>
    <div v-if="isVirtual && loading" class="tn-meta" style="padding:4px 8px">加载中…</div>
    <div v-if="node.has_children || isVirtual" class="tn-children" :style="isVirtual ? 'padding-left:0' : ''">
      <TreeNode v-for="child in children ?? []" :key="child.path"
                :conn="conn" :node="child" :depth="depth + 1" />
    </div>
  </div>
</template>
