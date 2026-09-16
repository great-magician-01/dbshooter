<script setup lang="ts">
/**
 * 全局右键菜单:单例渲染,状态在 utils/contextMenu(各组件调 showContextMenu)。
 * 挂载于 App.vue 顶层,Teleport 到 body。
 */
import { onBeforeUnmount, onMounted } from 'vue'

import AppIcon from '@/components/AppIcon.vue'
import { closeContextMenu, ctxMenuState, type MenuItem } from '@/utils/contextMenu'

function run(item: MenuItem) {
  closeContextMenu()
  item.action()
}

function onDocMouseDown() { closeContextMenu() }

onMounted(() => {
  document.addEventListener('mousedown', onDocMouseDown)
  window.addEventListener('scroll', closeContextMenu, true)   // 捕获:容器内滚动也关闭
  window.addEventListener('blur', closeContextMenu)
  window.addEventListener('resize', closeContextMenu)
})
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocMouseDown)
  window.removeEventListener('scroll', closeContextMenu, true)
  window.removeEventListener('blur', closeContextMenu)
  window.removeEventListener('resize', closeContextMenu)
})
</script>

<template>
  <Teleport to="body">
    <div v-if="ctxMenuState.visible" class="ctx-menu"
         :style="{ left: ctxMenuState.x + 'px', top: ctxMenuState.y + 'px' }">
      <button v-for="item in ctxMenuState.items" :key="item.label" class="ctx-item"
              @mousedown.stop @click="run(item)">
        <AppIcon v-if="item.icon" :name="item.icon" :size="12" />
        <span>{{ item.label }}</span>
      </button>
    </div>
  </Teleport>
</template>
