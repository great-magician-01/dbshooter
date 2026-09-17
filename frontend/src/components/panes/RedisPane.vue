<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { get, post } from '@/api/http'
import AppIcon from '@/components/AppIcon.vue'
import ResultGrid from '@/components/ResultGrid.vue'
import { useConnectionsStore } from '@/stores/connections'
import type { MetaNode, Tab } from '@/types'

const props = defineProps<{ tab: Tab }>()

const conns = useConnectionsStore()
const keys = ref<MetaNode[]>([])
const dbIndex = ref(0)
const keyFilter = ref('')
const selected = ref<any>(null)
const cmdInput = ref('')
const cmdOutput = ref<string[]>([])
const status = ref('')

const TYPE_COLOR: Record<string, string> = {
  hash: '#4d9de0', string: '#4caf7d', zset: '#d9a03f', list: '#b07fd9', set: '#e0708a',
}

const keyRows = computed(() =>
  keys.value.filter(k => !keyFilter.value || k.label.includes(keyFilter.value)))

const CMD_OUTPUT_CAP = 500   // 命令输出只保留最近若干行

/** 追加命令输出并截断,避免长输出无限占用内存 */
function pushOutput(...lines: string[]) {
  cmdOutput.value.push(...lines)
  if (cmdOutput.value.length > CMD_OUTPUT_CAP)
    cmdOutput.value = cmdOutput.value.slice(-CMD_OUTPUT_CAP)
}

/** 加载某一层级的叶子键;prefix 非空时加载该分组(如 user:)下的键 */
async function loadKeys(prefix = '') {
  status.value = 'SCAN 加载中…'
  try {
    // 树接口按分组返回;这里拉平当前层的叶子键
    const path = `db${dbIndex.value}${prefix ? `/${prefix}` : ''}`
    const nodes = await conns.metadata(props.tab.connection_id!, path)
    keys.value = nodes.filter(n => n.kind === 'key')
    status.value = prefix
      ? `已加载分组 ${prefix} 下 ${keys.value.length} 键`
      : `已加载 ${keys.value.length} 键(分组节点请在左树浏览)`
  } catch (e: any) {
    status.value = e.message
  }
}

async function selectKey(node: MetaNode) {
  const key = node.extra?.key ?? node.label
  try {
    selected.value = await get(`/api/connections/${props.tab.connection_id}/key`,
      { db: dbIndex.value, key })
  } catch (e: any) {
    selected.value = { key, type: 'error', value: e.message, ttl_ms: -2 }
  }
}

async function runCmd() {
  const stmt = cmdInput.value.trim()
  if (!stmt) return
  pushOutput(`> ${stmt}`)
  try {
    const { results } = await post<{ results: any[] }>('/api/query/execute',
      { conn_id: props.tab.connection_id, stmt })
    const r = results[0]
    if (r.error) pushOutput(`(error) ${r.error}`)
    else pushOutput(...(r.rows as any[][]).slice(0, CMD_OUTPUT_CAP).map(row => String(row[0])))
  } catch (e: any) {
    pushOutput(`(error) ${e.message}`)
  }
  cmdInput.value = ''
}

onMounted(async () => {
  if (props.tab.context?.db) dbIndex.value = parseInt(String(props.tab.context.db).replace('db', '')) || 0
  const key = String(props.tab.context?.key ?? '')
  // 含冒号的键在左树里归属分组节点,顶层 SCAN 找不到:先加载它所在的父分组再选中
  const group = key.includes(':') ? key.slice(0, key.lastIndexOf(':') + 1) : ''
  await loadKeys(group)
  if (key) {
    const hit = keys.value.find(k => (k.extra?.key ?? k.label) === key)
    if (hit) selectKey(hit)
  }
})
</script>

<template>
  <section class="pane">
    <div class="pane-toolbar">
      <span class="tn-ico"><AppIcon name="redis" /></span><b style="font-size:12.5px">{{ tab.title }}</b>
      <select v-model.number="dbIndex" style="height:26px;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:4px" @change="loadKeys()">
        <option v-for="i in 16" :key="i - 1" :value="i - 1">db{{ i - 1 }}</option>
      </select>
      <button class="pt-btn" @click="loadKeys()">刷新</button>
      <div class="pt-conn" style="font-family:var(--mono)">{{ status }}</div>
    </div>
    <div class="redis-cmd">
      <input v-model="cmdInput" placeholder="Redis 命令,如 HGETALL user:1001(回车执行;只读连接禁写)"
             @keyup.enter="runCmd">
    </div>
    <div v-if="cmdOutput.length" style="max-height:140px;overflow:auto;border-bottom:1px solid var(--border-soft)">
      <pre class="log" style="padding:8px 14px">{{ cmdOutput.join('\n') }}</pre>
    </div>
    <div class="redis-body">
      <div class="rkey-list">
        <div class="rkey-filter"><input v-model="keyFilter" placeholder="过滤已加载键"></div>
        <div class="rkeys">
          <div v-for="k in keyRows" :key="k.path" class="rkey"
               :class="{ sel: selected?.key === (k.extra?.key ?? k.label) }"
               @click="selectKey(k)">
            <span class="kn">{{ k.label }}</span>
          </div>
          <div v-if="!keyRows.length" class="tn-empty">{{ status || '(无键)' }}</div>
        </div>
      </div>
      <div class="rval">
        <div class="rval-head" v-if="selected">
          <span class="kt" :style="{ background: TYPE_COLOR[selected.type] ?? 'var(--muted)',
                width:'16px',height:'16px',borderRadius:'3px',display:'grid',placeItems:'center',
                fontSize:'9px',fontWeight:700,color:'#fff' }">
            {{ (selected.type || '?')[0].toUpperCase() }}
          </span>
          <b>{{ selected.key }}</b>
          <span class="meta">类型 {{ selected.type }} · TTL
            {{ selected.ttl_ms >= 0 ? (selected.ttl_ms / 1000).toFixed(0) + 's' : '不过期' }}</span>
        </div>
        <div class="rval-body">
          <template v-if="selected">
            <pre v-if="selected.type === 'string'" class="plan"
                 style="margin:12px 14px;background:var(--bg);border:1px solid var(--border);border-radius:6px">{{ selected.value }}</pre>
            <ResultGrid v-else-if="selected.type === 'hash'"
                        :columns="[{ name: 'field', type: '' }, { name: 'value', type: '' }]"
                        :rows="Object.entries(selected.value)" />
            <ResultGrid v-else-if="selected.type === 'zset'"
                        :columns="[{ name: 'member', type: '' }, { name: 'score', type: '' }]"
                        :rows="selected.value" />
            <ResultGrid v-else :columns="[{ name: 'value', type: '' }]"
                        :rows="(Array.isArray(selected.value) ? selected.value : [selected.value]).map((v: any) => [v])" />
          </template>
          <div v-else class="result-placeholder">选择左侧键查看值,或在上方直接执行命令</div>
        </div>
      </div>
    </div>
  </section>
</template>
