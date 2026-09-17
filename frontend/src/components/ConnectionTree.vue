<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import AppIcon from '@/components/AppIcon.vue'
import TreeNode from '@/components/TreeNode.vue'
import { useConnectionsStore } from '@/stores/connections'
import { useUiStore } from '@/stores/ui'
import { useWorkspaceStore } from '@/stores/workspace'
import { showContextMenu, type MenuItem } from '@/utils/contextMenu'
import { toast } from '@/utils/toast'

const conns = useConnectionsStore()
const ui = useUiStore()
const workspace = useWorkspaceStore()
const filter = ref('')

const BADGE: Record<string, [string, string]> = {
  mysql: ['My', 'var(--c-mysql)'], pg: ['Pg', 'var(--c-pg)'], sqlite: ['Sq', 'var(--c-sqlite)'],
  redis: ['Re', 'var(--c-redis)'], mongo: ['Mg', 'var(--c-mongo)'],
}

const filtered = computed(() => {
  const q = filter.value.trim().toLowerCase()
  if (!q) return conns.items
  return conns.items.filter(c => c.name.toLowerCase().includes(q))
})

function openConnTab(conn: { id: string; type: string; name: string }) {
  // 双击连接:SQL 类开编辑器;Redis/Mongo 开各自工作台
  if (conn.type === 'redis') {
    workspace.addTab({ type: 'redis', title: '键浏览', connection_id: conn.id })
  } else if (conn.type === 'mongo') {
    workspace.addTab({ type: 'mongo', title: conn.name, connection_id: conn.id })
  } else {
    workspace.addTab({ type: 'sql', title: undefined, connection_id: conn.id })
  }
}

/** 删除连接:先二次确认(会连带清掉保存的密码,误点代价大) */
async function removeConn(conn: { id: string; name: string }) {
  if (!confirm(`确定删除连接「${conn.name}」吗?该操作不可撤销。`)) return
  try {
    await conns.remove(conn.id)
  } catch (e: any) {
    toast(e.message, 'err')
  }
}

/** 右键连接行:新建对应类型的工作台标签页(动作同双击) */
function onConnMenu(e: MouseEvent, conn: { id: string; type: string; name: string }) {
  const label = conn.type === 'redis' ? '新建键浏览标签页'
    : conn.type === 'mongo' ? '新建查询标签页' : '新建 SQL 标签页'
  const icon = conn.type === 'redis' ? 'redis' : conn.type === 'mongo' ? 'mongo' : 'sql'
  const items: MenuItem[] = [{ label, icon, action: () => openConnTab(conn) }]
  showContextMenu(e, items)
}

onMounted(() => { if (!conns.items.length) conns.load() })
</script>

<template>
  <div class="side-head">
    <span class="side-title">数据库连接</span>
    <button class="icon-btn" title="新建连接" @click="ui.openConnDialog(null)">
      <svg width="12" height="12" viewBox="0 0 12 12"><path d="M6 1v10M1 6h10" stroke="currentColor" stroke-width="1.4" /></svg>
    </button>
  </div>
  <div class="side-search">
    <input v-model="filter" placeholder="过滤连接与对象…">
  </div>
  <div class="tree">
    <div v-if="!filtered.length" class="tn-empty">
      还没有连接<br>
      <button class="btn" style="margin-top:8px" @click="ui.openConnDialog(null)">新建第一个连接</button>
    </div>
    <div v-for="conn in filtered" :key="conn.id" class="tn open">
      <div class="tn-row" @dblclick="openConnTab(conn)"
           @contextmenu.prevent="onConnMenu($event, conn)">
        <span class="tn-arrow" style="visibility:hidden"><AppIcon name="caret" :size="10" /></span>
        <span class="dbbadge" :style="{ background: BADGE[conn.type]?.[1] }">{{ BADGE[conn.type]?.[0] }}</span>
        <span class="tn-label" :title="conn.name">{{ conn.name }}</span>
        <span class="tn-meta">{{ conn.type }}</span>
        <span class="tn-actions">
          <button class="icon-btn" title="编辑连接" @click.stop="ui.openConnDialog(conn.id)"><AppIcon name="edit" :size="12" /></button>
          <button class="icon-btn" title="删除连接"
                  @click.stop="removeConn(conn)"><AppIcon name="close" :size="12" /></button>
        </span>
      </div>
      <div class="tn-children">
        <TreeNode :conn="conn" :node="{ path: '', label: '', kind: 'database', has_children: true, extra: {} }"
                  :depth="0" />
      </div>
    </div>
  </div>
</template>
