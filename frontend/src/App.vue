<script setup lang="ts">
import { onMounted } from 'vue'

import AiPanel from '@/components/AiPanel.vue'
import ConnectionDialog from '@/components/dialogs/ConnectionDialog.vue'
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

const theme = useThemeStore()
const ui = useUiStore()
const conns = useConnectionsStore()
const wsStore = useWorkspaceStore()
const ai = useAiStore()

onMounted(async () => {
  theme.init()
  await Promise.all([conns.load(), wsStore.load(), ai.loadProviders()])
  ai.loadSessions()
})
</script>

<template>
  <div class="app-shell">
    <TopBar />
    <div class="app-middle">
      <aside id="sidebar"><ConnectionTree /></aside>
      <div class="vsplit" />
      <main id="workspace"><TabWorkspace /></main>
      <div v-if="ui.aiVisible" class="vsplit" />
      <aside v-if="ui.aiVisible" id="aipanel"><AiPanel /></aside>
    </div>
    <StatusBar />
    <ConnectionDialog v-if="ui.connDialogVisible" />
    <SettingsDialog v-if="ui.settingsVisible" />
  </div>
</template>
