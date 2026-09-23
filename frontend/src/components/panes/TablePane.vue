<script setup lang="ts">
/**
 * 表详情页签(双击树的表/视图节点打开):数据 | 结构 | DDL | ER 四个子页。
 * - 数据:直接复用 DataPane(context 键 table/ref/path 完全兼容);
 * - 结构:列元数据表格(名称/类型/主键/可空/默认值/注释);
 * - DDL:只读 CodeMirror 展示;
 * - ER:外键关系图(ErDiagram,首次点击才加载)。
 * 子页用 touched 集合懒挂载 + v-show 保活:切回不重查、状态不丢。
 */
import { onMounted, reactive, ref } from 'vue'

import { get } from '@/api/http'
import AppIcon from '@/components/AppIcon.vue'
import CodeEditor from '@/components/CodeEditor.vue'
import ErDiagram from '@/components/ErDiagram.vue'
import DataPane from '@/components/panes/DataPane.vue'
import type { ColumnInfo, Tab } from '@/types'

const props = defineProps<{ tab: Tab }>()

type Sub = 'data' | 'structure' | 'ddl' | 'er'
const SUBS: { key: Sub; label: string; icon: string }[] = [
  { key: 'data', label: '数据', icon: 'table' },
  { key: 'structure', label: '结构', icon: 'column' },
  { key: 'ddl', label: 'DDL', icon: 'sql' },
  { key: 'er', label: 'ER', icon: 'er' },
]
const sub = ref<Sub>('data')
const touched = reactive<Set<Sub>>(new Set(['data']))
function activate(s: Sub) { sub.value = s; touched.add(s) }

const columns = ref<ColumnInfo[]>([])
const ddl = ref('')
const structError = ref('')
const ddlError = ref('')
const loading = ref(true)

async function loadMeta() {
  const cid = props.tab.connection_id
  const path = (props.tab.context.path as string) || ''
  if (!cid || !path) { structError.value = '页签缺少表路径'; loading.value = false; return }
  loading.value = true
  // 结构与 DDL 各自独立报错,互不阻塞
  await Promise.all([
    get<{ columns: ColumnInfo[] }>(`/api/connections/${cid}/structure`, { path })
      .then(r => { columns.value = r.columns })
      .catch((e: any) => { structError.value = e.message }),
    // 标量传参:axios 数组会序列化成 tables[]=x,FastAPI list Query 收不到
    get<{ ddl: string }>(`/api/connections/${cid}/ddl`, { tables: path })
      .then(r => { ddl.value = r.ddl })
      .catch((e: any) => { ddlError.value = e.message }),
  ])
  loading.value = false
}

onMounted(loadMeta)
</script>

<template>
  <section class="pane">
    <div class="result-tabs">
      <span v-for="s in SUBS" :key="s.key" class="rt-tab" :class="{ active: sub === s.key }"
            @click="activate(s.key)">
        <AppIcon :name="s.icon" :size="12" /> {{ s.label }}
      </span>
      <span class="result-status">{{ tab.context.path }}</span>
    </div>

    <!-- 数据:嵌套 pane 需相对定位锚点(.pane 是 absolute inset:0) -->
    <div v-if="touched.has('data')" v-show="sub === 'data'" class="tp-sub">
      <DataPane :tab="tab" />
    </div>

    <!-- 结构:列元数据表格 -->
    <div v-if="touched.has('structure')" v-show="sub === 'structure'" class="result-body">
      <div v-if="structError" class="result-placeholder" style="color:var(--red)">
        {{ structError }}</div>
      <div v-else-if="loading" class="result-placeholder">加载中…</div>
      <div v-else-if="!columns.length" class="result-placeholder">没有列元数据</div>
      <div v-else class="grid-scroll">
        <table class="grid">
          <thead>
            <tr><th>#</th><th>列名</th><th>类型</th><th>主键</th><th>可空</th><th>默认值</th>
              <th>注释</th></tr>
          </thead>
          <tbody>
            <tr v-for="(c, i) in columns" :key="c.name">
              <td class="rn">{{ i + 1 }}</td>
              <td>{{ c.name }}</td>
              <td style="color:var(--text2)">{{ c.type }}</td>
              <td><AppIcon v-if="c.pk" name="pk" :size="12" /></td>
              <td>{{ c.nullable ? 'YES' : 'NO' }}</td>
              <td><span v-if="c.default === null" class="null">NULL</span>
                <template v-else>{{ c.default }}</template></td>
              <td style="color:var(--text2)">{{ c.comment }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- DDL:只读编辑器展示 -->
    <div v-if="touched.has('ddl')" v-show="sub === 'ddl'" class="result-body"
         style="display:flex;flex-direction:column;overflow:hidden">
      <div v-if="ddlError" class="result-placeholder" style="color:var(--red)">{{ ddlError }}</div>
      <div v-else-if="loading" class="result-placeholder">加载中…</div>
      <div v-else-if="!ddl" class="result-placeholder">该对象没有 DDL</div>
      <CodeEditor v-else :model-value="ddl" lang="sql" readonly />
    </div>

    <!-- ER:首次点击才加载 -->
    <div v-if="touched.has('er')" v-show="sub === 'er'" class="tp-sub">
      <ErDiagram :tab="tab" />
    </div>
  </section>
</template>
