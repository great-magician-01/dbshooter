<script setup lang="ts">
import { computed } from 'vue'

import { ICON_PATHS } from '@/components/icons'

/** 内联 SVG 图标,路径集中维护于 icons.ts */
const props = withDefaults(defineProps<{ name: string; size?: number }>(), { size: 13 })

const html = computed(() => ICON_PATHS[props.name] ?? ICON_PATHS.dot)
</script>

<template>
  <svg class="app-icon" :width="size" :height="size" viewBox="0 0 16 16" fill="none"
       stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"
       aria-hidden="true" v-html="html" />
</template>
