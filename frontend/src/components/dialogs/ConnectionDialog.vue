<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { useConnectionsStore } from '@/stores/connections'
import { useUiStore } from '@/stores/ui'
import { toast } from '@/utils/toast'

const ui = useUiStore()
const conns = useConnectionsStore()

const TYPES = [
  { key: 'mysql', label: 'MySQL' }, { key: 'pg', label: 'PostgreSQL' },
  { key: 'sqlite', label: 'SQLite' }, { key: 'redis', label: 'Redis' },
  { key: 'mongo', label: 'MongoDB' },
] as const
const BADGE: Record<string, [string, string]> = {
  mysql: ['My', 'var(--c-mysql)'], pg: ['Pg', 'var(--c-pg)'], sqlite: ['Sq', 'var(--c-sqlite)'],
  redis: ['Re', 'var(--c-redis)'], mongo: ['Mg', 'var(--c-mongo)'],
}

const form = reactive({
  id: null as string | null,
  type: 'mysql' as string,
  name: '', host: '127.0.0.1', port: 3306, database: '', username: 'root',
  password: '', path: '', uri: '', db: 0, readonly: false,
})
const testing = ref(false)
const testMsg = ref<{ ok: boolean; text: string } | null>(null)

// 不同数据库的字段集
const fieldSet = computed(() => {
  switch (form.type) {
    case 'sqlite': return ['name', 'path', 'readonly']
    case 'redis': return ['name', 'host', 'port', 'password', 'db', 'readonly']
    case 'mongo': return ['name', 'host', 'port', 'database', 'username', 'password', 'uri', 'readonly']
    default: return ['name', 'host', 'port', 'database', 'username', 'password', 'readonly']
  }
})
const show = (f: string) => fieldSet.value.includes(f)

onMounted(() => {
  if (ui.editingConnection) {
    const c = conns.items.find(c => c.id === ui.editingConnection)
    if (c) {
      form.id = c.id; form.type = c.type; form.name = c.name
      form.host = c.host || '127.0.0.1'; form.port = c.port ?? defaultPort(c.type)
      form.database = c.database; form.username = c.username
      form.path = c.params?.path ?? ''; form.uri = c.params?.uri ?? ''
      form.db = c.params?.db ?? 0; form.readonly = c.readonly
      // 密码不回填,留空 = 不修改
    }
  }
})

function defaultPort(t: string) {
  return { mysql: 3306, pg: 5432, redis: 6379, mongo: 27017 }[t] ?? 0
}

function pickType(t: string) {
  form.type = t
  form.port = defaultPort(t)
}

function buildPayload() {
  const params: Record<string, any> = {}
  if (form.type === 'sqlite') params.path = form.path
  if (form.type === 'redis') params.db = form.db
  if (form.type === 'mongo' && form.uri) params.uri = form.uri
  return {
    id: form.id, name: form.name || `${form.type}-${form.host || form.path}`,
    type: form.type, host: form.host, port: form.port, database: form.database,
    username: form.username, password: form.password, params, readonly: form.readonly,
  }
}

async function test() {
  testing.value = true
  testMsg.value = null
  try {
    const r = await conns.test({ config: buildPayload() })
    testMsg.value = { ok: r.ok, text: r.ok ? `✓ ${r.message}` : `✗ ${r.message}` }
  } catch (e: any) {
    testMsg.value = { ok: false, text: `✗ ${e.message}` }
  } finally {
    testing.value = false
  }
}

async function save() {
  try {
    await conns.save(buildPayload())
    toast('连接已保存', 'ok')
    ui.connDialogVisible = false
  } catch (e: any) {
    toast(e.message, 'err')
  }
}
</script>

<template>
  <div class="modal-mask" @click.self="ui.connDialogVisible = false">
    <div class="modal">
      <div class="modal-head">{{ form.id ? '编辑连接' : '新建数据库连接' }}
        <button class="icon-btn" @click="ui.connDialogVisible = false">✕</button>
      </div>
      <div class="modal-body">
        <div class="db-cards">
          <div v-for="t in TYPES" :key="t.key" class="db-card" :class="{ sel: form.type === t.key }"
               @click="pickType(t.key)">
            <span class="dbbadge" :style="{ background: BADGE[t.key][1] }">{{ BADGE[t.key][0] }}</span>
            {{ t.label }}
          </div>
        </div>
        <div class="form-grid">
          <div v-if="show('name')" class="field full"><label>连接名</label><input v-model="form.name" placeholder="留空自动生成"></div>
          <div v-if="show('path')" class="field full"><label>数据库文件路径</label><input v-model="form.path" placeholder="D:\data\demo.db 或 :memory:"></div>
          <div v-if="show('host')" class="field"><label>主机</label><input v-model="form.host"></div>
          <div v-if="show('port')" class="field"><label>端口</label><input v-model.number="form.port" type="number"></div>
          <div v-if="show('database')" class="field"><label>数据库</label><input v-model="form.database"></div>
          <div v-if="show('username')" class="field"><label>用户名</label><input v-model="form.username"></div>
          <div v-if="show('password')" class="field"><label>密码{{ form.id ? '(留空不修改)' : '' }}</label><input v-model="form.password" type="password"></div>
          <div v-if="show('db')" class="field"><label>DB 索引</label><input v-model.number="form.db" type="number"></div>
          <div v-if="show('uri')" class="field full"><label>连接 URI(可选,优先于主机/端口)</label><input v-model="form.uri" placeholder="mongodb://user:pass@host:27017/db"></div>
          <div v-if="show('readonly')" class="field full">
            <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
              <input v-model="form.readonly" type="checkbox"> 只读模式(拦截一切写操作)
            </label>
          </div>
        </div>
      </div>
      <div class="modal-foot">
        <button class="btn" :disabled="testing" @click="test">{{ testing ? '连接中…' : '测试连接' }}</button>
        <span class="test-msg" :class="testMsg?.ok ? 'ok' : 'err'">{{ testMsg?.text }}</span>
        <span class="sp" />
        <button class="btn" @click="ui.connDialogVisible = false">取消</button>
        <button class="btn primary" @click="save">保存</button>
      </div>
    </div>
  </div>
</template>
