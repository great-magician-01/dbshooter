<script setup lang="ts">
import { computed } from 'vue'

import AppIcon from '@/components/AppIcon.vue'
import DataPane from '@/components/panes/DataPane.vue'
import MongoPane from '@/components/panes/MongoPane.vue'
import RedisPane from '@/components/panes/RedisPane.vue'
import SqlPane from '@/components/panes/SqlPane.vue'
import { useConnectionsStore } from '@/stores/connections'
import { useWorkspaceStore } from '@/stores/workspace'
import type { Tab, TabType } from '@/types'

const workspace = useWorkspaceStore()
const conns = useConnectionsStore()

const PANE: Record<TabType, any> = { sql: SqlPane, data: DataPane, redis: RedisPane, mongo: MongoPane }
const TICON: Record<TabType, string> = { sql: 'sql', data: 'table', redis: 'redis', mongo: 'mongo' }

function subOf(tab: Tab): string {
  if (tab.connection_id) {
    const c = conns.items.find(c => c.id === tab.connection_id)
    if (c) return c.name
  }
  return ''
}

const active = computed(() => workspace.activeTab)
</script>

<template>
  <div class="tabbar">
    <div v-for="tab in workspace.tabs" :key="tab.id" class="tab"
         :class="{ active: tab.id === workspace.activeId }"
         @click="workspace.activate(tab.id)">
      <span class="tn-ico"><AppIcon :name="TICON[tab.type]" /></span>
      <span>{{ tab.title }}</span>
      <span v-if="subOf(tab)" class="tab-sub">{{ subOf(tab) }}</span>
      <span class="tab-close" title="关闭"
            @click.stop="workspace.closeTab(tab.id)"><AppIcon name="close" :size="11" /></span>
    </div>
  </div>
  <div class="panes">
    <div v-if="!active" class="empty-pane">
      <div class="box">
        <h2>没有打开的编辑器</h2>
        <p>双击左侧树中的表、集合或键开始浏览，<br>或点顶栏 <b>SQL</b> 新建编辑器。</p>
      </div>
    </div>
    <!-- max=10:LRU 裁剪缓存实例,已关闭的页签不会永久持有 CodeMirror 与结果集 -->
    <KeepAlive :max="10">
      <component :is="PANE[active.type]" v-if="active" :key="active.id" :tab="active" />
    </KeepAlive>
  </div>
</template>
