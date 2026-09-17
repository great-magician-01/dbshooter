<script setup lang="ts">
import { onMounted } from 'vue'

import AiPanel from '@/components/AiPanel.vue'
import ConnectionDialog from '@/components/dialogs/ConnectionDialog.vue'
import ContextMenu from '@/components/ContextMenu.vue'
import SettingsDialog from '@/components/dialogs/SettingsDialog.vue'
import ConnectionTree from '@/components/ConnectionTree.vue'
import StatusBar from '@/components/StatusBar.vue'
import TabWorkspace from '@/components/TabWorkspace.vue'
import TopBar from '@/components/TopBar.vue'
import { useAiStore } from '@/stores/ai'
import { useConnectionsStore } from '@/stores/connections'
import { useThemeStore } from '@/stores/theme'
import { useUiStore } from '@/stores/ui'
import { useWorkspaceStore } from '@/stores/workspace'
import { useSplitter } from '@/utils/split'
import { toast } from '@/utils/toast'

const theme = useThemeStore()
const ui = useUiStore()
const conns = useConnectionsStore()
const wsStore = useWorkspaceStore()
const ai = useAiStore()

// 侧栏 / AI 面板宽度:分隔条可拖拽(见 .vsplit)
const { size: sidebarWidth, onPointerDown: sidebarSplit } = useSplitter(264, {
  axis: 'x', min: 180, max: 560, storageKey: 'ds-sidebar-w',
})
const { size: aiWidth, onPointerDown: aiSplit } = useSplitter(360, {
  axis: 'x', side: 'end', min: 280, max: 680, storageKey: 'ds-ai-w',
})

onMounted(async () => {
  theme.init()
  try {
    await Promise.all([conns.load(), wsStore.load(), ai.loadProviders()])
    await ai.loadSessions()
  } catch (e: any) {
    // 启动加载失败必须出声:401 几乎都是访问令牌不对(服务端设了 DBSHOOTER_TOKEN)
    toast(e?.status === 401 ? '加载失败:请到设置里检查访问令牌' : `加载失败:${e?.message ?? '未知错误'}`, 'err')
  }
})
</script>

<template>
  <div class="app-shell">
    <TopBar />
    <div class="app-middle">
      <aside id="sidebar" :style="{ width: sidebarWidth + 'px' }"><ConnectionTree /></aside>
      <div class="vsplit" @pointerdown="sidebarSplit" />
      <main id="workspace"><TabWorkspace /></main>
      <div v-if="ui.aiVisible" class="vsplit" @pointerdown="aiSplit" />
      <aside v-if="ui.aiVisible" id="aipanel" :style="{ width: aiWidth + 'px' }"><AiPanel /></aside>
    </div>
    <StatusBar />
    <ContextMenu />
    <ConnectionDialog v-if="ui.connDialogVisible" />
    <SettingsDialog v-if="ui.settingsVisible" />
  </div>
</template>
