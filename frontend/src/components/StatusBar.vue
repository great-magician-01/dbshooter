<script setup lang="ts">
import { computed } from 'vue'

import { useAiStore } from '@/stores/ai'
import { useConnectionsStore } from '@/stores/connections'
import { useWorkspaceStore } from '@/stores/workspace'
import { formatMs } from '@/utils/format'

const workspace = useWorkspaceStore()
const conns = useConnectionsStore()
const ai = useAiStore()

const activeTab = computed(() => workspace.activeTab)
const activeConn = computed(() =>
  conns.items.find(c => c.id === activeTab.value?.connection_id) ?? null)
const provider = computed(() => ai.activeProvider)
</script>

<template>
  <footer id="statusbar">
    <span><span class="dot" /> {{ activeConn ? `${activeConn.name} · ${activeConn.type}` : '未选择连接' }}</span>
    <span v-if="activeTab">{{ activeTab.title }}</span>
    <span class="sp" />
    <span v-if="workspace.lastRun">{{ workspace.lastRun.rows }} 行 · {{ formatMs(workspace.lastRun.ms) }}</span>
    <span>AI: {{ provider ? provider.name + ' ✓' : '未配置' }}</span>
  </footer>
</template>
