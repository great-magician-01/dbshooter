<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, toRef, watch } from 'vue'

import type { Column } from '@/types'
import { cellText, isNullCell } from '@/utils/format'
import { nearBottom } from '@/utils/scroll'
import { estimateColWidths, useVirtualList } from '@/utils/virtualList'

const props = defineProps<{
  columns: Column[]
  rows: any[][]
}>()

/** 滚动到底(或内容不足一屏)时通知父组件;有服务端分页的场景(SqlPane)据此加载下一页 */
const emit = defineEmits<{ (e: 'reach-end'): void }>()

const scrollEl = ref<HTMLElement>()
// 行高初值 29px = 12px 字体 * 1.5 行高 + 上下 padding 10px + 边框 1px,挂载后按实测修正
const { rowH, virtual, visible, topPad, bottomPad, onScroll, syncViewport, observeViewport } =
  useVirtualList(toRef(props, 'rows'), { rowHeight: 29, threshold: 500 })

/** 断开视口 ResizeObserver 的清理函数 */
let stopObserveViewport: (() => void) | null = null

/**
 * 虚拟模式的列宽(ch):table-layout:fixed 需要显式宽度。
 * 固定取样前 50 行(append-only,内容不变),保证数据追加后列宽稳定不跳动。
 */
const colWidths = computed(() => {
  if (!virtual.value) return []
  const headers = props.columns.map(c => (c.type ? `${c.name} ${c.type}` : c.name))
  const sample = props.rows.slice(0, 50).map(r => r.map(cellText))
  const rn = Math.min(8, Math.max(4, String(props.rows.length).length + 2))
  return [rn, ...estimateColWidths(headers, sample)]
})

function handleScroll(e: Event) {
  onScroll(e)
  if (nearBottom(e.target as HTMLElement)) emit('reach-end')
}

/** 实测行高,修正初值与真实渲染(缩放/字体)的偏差 */
function measureRow() {
  const tr = scrollEl.value?.querySelector('tbody tr:not(.vs-pad)')
  const h = tr?.getBoundingClientRect().height
  if (h && Math.abs(h - rowH.value) > 1) rowH.value = h
}

// 数据变化后:修正行高;内容不足一屏(无滚动条、永远触发不了滚动)时让父组件继续补拉
watch(() => props.rows.length, async () => {
  await nextTick()
  measureRow()
  checkEnd()
})

// 组件由 v-if 创建时首屏数据已就位,watch 不会触发,挂载后主动检查一次
onMounted(async () => {
  await nextTick()
  syncViewport(scrollEl.value)
  // 容器尺寸变化(拖拽结果区高度 / 窗口缩放)时重算窗口
  stopObserveViewport = observeViewport(scrollEl.value)
  measureRow()
  checkEnd()
})

onBeforeUnmount(() => { stopObserveViewport?.(); stopObserveViewport = null })

function checkEnd() {
  const el = scrollEl.value
  if (el && nearBottom(el)) emit('reach-end')
}
</script>

<template>
  <div ref="scrollEl" class="grid-scroll" @scroll.passive="handleScroll">
    <table class="grid" :class="{ virtual }">
      <colgroup v-if="virtual">
        <col v-for="(w, i) in colWidths" :key="i" :style="{ width: w + 'ch' }">
      </colgroup>
      <thead>
        <tr>
          <th style="text-align:right">#</th>
          <th v-for="col in columns" :key="col.name">
            {{ col.name }}<span v-if="col.type" class="col-type">{{ col.type }}</span>
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="topPad" class="vs-pad">
          <td :colspan="columns.length + 1" :style="{ height: topPad + 'px' }" />
        </tr>
        <tr v-for="{ item: row, index } in visible" :key="index">
          <td class="rn">{{ index + 1 }}</td>
          <td v-for="(v, j) in row" :key="j" :title="virtual ? cellText(v) : undefined">
            <span v-if="isNullCell(v)" class="null">NULL</span>
            <template v-else>{{ cellText(v) }}</template>
          </td>
        </tr>
        <tr v-if="bottomPad" class="vs-pad">
          <td :colspan="columns.length + 1" :style="{ height: bottomPad + 'px' }" />
        </tr>
      </tbody>
    </table>
  </div>
</template>
