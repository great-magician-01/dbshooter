<script setup lang="ts">
import { useThemeStore } from '@/stores/theme'
import { useUiStore } from '@/stores/ui'
import { useWorkspaceStore } from '@/stores/workspace'
import AppIcon from '@/components/AppIcon.vue'

const ui = useUiStore()
const theme = useThemeStore()
const workspace = useWorkspaceStore()

function newSqlTab() {
  const active = workspace.activeTab
  workspace.addTab({ type: 'sql', connection_id: active?.connection_id ?? null })
}
</script>

<template>
  <header id="topbar">
    <div class="logo">
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
        <circle cx="10" cy="10" r="8.2" stroke="#4d9de0" stroke-width="1.6" />
        <circle cx="10" cy="10" r="2.4" fill="#4d9de0" />
        <path d="M10 0v4M10 16v4M0 10h4M16 10h4" stroke="#4d9de0" stroke-width="1.6" />
      </svg>
      <span><b>DB</b>Shooter</span>
    </div>
    <button class="tb-btn primary" @click="ui.openConnDialog(null)">
      <svg width="12" height="12" viewBox="0 0 12 12"><path d="M6 1v10M1 6h10" stroke="currentColor" stroke-width="1.6" /></svg>
      新建连接
    </button>
    <button class="tb-btn" @click="newSqlTab">
      <svg width="12" height="12" viewBox="0 0 12 12"><path d="M6 1v10M1 6h10" stroke="currentColor" stroke-width="1.6" /></svg>
      SQL
    </button>
    <div class="tb-sep" />
    <button class="tb-btn" title="执行 (Ctrl+Enter)" @click="ui.triggerExecute()">
      <svg width="11" height="11" viewBox="0 0 11 11"><path d="M2.5 1.5l7 4-7 4z" fill="#4caf7d" /></svg>
      执行
    </button>
    <div class="tb-right">
      <button class="tb-btn" :class="{ toggled: ui.aiVisible }" @click="ui.aiVisible = !ui.aiVisible">
        <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
          <path d="M6.5 1l1.35 3.65L11.5 6 7.85 7.35 6.5 11 5.15 7.35 1.5 6l3.65-1.35z" fill="currentColor" />
        </svg>
        AI 助手
      </button>
      <button class="tb-btn" title="切换暗/亮主题" @click="theme.toggle()">
        <template v-if="theme.mode === 'dark'"><AppIcon name="moon" :size="12" /> 亮色</template>
        <template v-else><AppIcon name="sun" :size="12" /> 暗色</template>
      </button>
      <button class="tb-btn" title="设置:AI Provider 管理" @click="ui.settingsVisible = true">设置</button>
    </div>
  </header>
</template>
