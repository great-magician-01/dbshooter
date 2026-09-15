<script setup lang="ts">
import { cellText, isNullCell } from '@/utils/format'
import type { Column } from '@/types'

const props = defineProps<{
  columns: Column[]
  rows: any[][]
  /** 渲染上限,超出提示截断 */
  cap?: number
}>()
</script>

<template>
  <table class="grid">
    <thead>
      <tr>
        <th style="text-align:right">#</th>
        <th v-for="col in columns" :key="col.name">
          {{ col.name }}<span v-if="col.type" class="col-type">{{ col.type }}</span>
        </th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="(row, i) in rows.slice(0, cap ?? 500)" :key="i">
        <td class="rn">{{ i + 1 }}</td>
        <td v-for="(v, j) in row" :key="j">
          <span v-if="isNullCell(v)" class="null">NULL</span>
          <template v-else>{{ cellText(v) }}</template>
        </td>
      </tr>
    </tbody>
  </table>
  <div v-if="rows.length > (cap ?? 500)" class="result-status" style="padding:6px 12px">
    已渲染前 {{ cap ?? 500 }} 行,共 {{ rows.length }} 行(滚动分页由后端缓冲提供)
  </div>
</template>
