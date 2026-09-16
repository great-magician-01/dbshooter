<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'

import AppIcon from '@/components/AppIcon.vue'
import { useAiStore } from '@/stores/ai'
import { useUiStore } from '@/stores/ui'
import { useWorkspaceStore } from '@/stores/workspace'
import { toast } from '@/utils/toast'

const ai = useAiStore()
const ui = useUiStore()
const workspace = useWorkspaceStore()

const question = ref('')
const newTable = ref('')
const provMenuOpen = ref(false)
const sessMenuOpen = ref(false)
const msgsEl = ref<HTMLElement>()

onMounted(() => { if (!ai.sessions.length) ai.loadSessions() })

function scrollBottom() {
  nextTick(() => { if (msgsEl.value) msgsEl.value.scrollTop = msgsEl.value.scrollHeight })
}

async function send() {
  const q = question.value.trim()
  if (!q || ai.generating) return
  question.value = ''
  await ai.ask(q, workspace.activeTab?.connection_id ?? null)
  scrollBottom()
  // 流式期间持续滚动
  const timer = setInterval(() => {
    scrollBottom()
    if (!ai.generating) clearInterval(timer)
  }, 200)
}

function addCtxTable() {
  const t = newTable.value.trim()
  if (t && !ai.ctxTables.includes(t)) ai.ctxTables.push(t)
  newTable.value = ''
}

function targetSqlTab() {
  const active = workspace.activeTab
  if (active?.type === 'sql') return active
  const any = workspace.tabs.find(t => t.type === 'sql')
  return any ?? workspace.addTab({ type: 'sql', connection_id: active?.connection_id ?? null })
}

function insertSql(sql: string, run: boolean) {
  const tab = targetSqlTab()
  const content = (tab.content ? tab.content.replace(/\s*$/, '') + '\n\n' : '') + sql + '\n'
  workspace.setContent(tab.id, content)
  if (workspace.activeId !== tab.id) {
    ui.pendingRunTabId = run ? tab.id : null
    workspace.activate(tab.id)
  } else if (run) {
    ui.triggerExecute()
  }
  toast(run ? '已插入,执行中…' : '已插入到 SQL 编辑器', 'ok')
}

function copySql(sql: string) {
  navigator.clipboard?.writeText(sql)
  toast('已复制', 'ok')
}

async function pickProvider(id: string) {
  provMenuOpen.value = false
  await ai.activateProvider(id)
  toast(`已切换生效 Provider:${ai.activeProvider?.name}`, 'ok')
}

async function pickSession(id: string) {
  sessMenuOpen.value = false
  await ai.selectSession(id)
  scrollBottom()
}

async function createSession() {
  sessMenuOpen.value = false
  await ai.newSession(workspace.activeTab?.connection_id ?? null)
}

function closeMenus(e: MouseEvent) {
  if (!(e.target as HTMLElement).closest('.dd')) {
    provMenuOpen.value = false
    sessMenuOpen.value = false
  }
}
onMounted(() => document.addEventListener('click', closeMenus))
</script>

<template>
  <div class="ai-head">
    <svg width="14" height="14" viewBox="0 0 13 13" fill="none">
      <path d="M6.5 1l1.35 3.65L11.5 6 7.85 7.35 6.5 11 5.15 7.35 1.5 6l3.65-1.35z" fill="#4d9de0" />
    </svg>
    AI 助手 · Text-to-SQL
    <div class="dd">
      <button class="icon-btn" title="会话历史(存 sqlite)" @click.stop="sessMenuOpen = !sessMenuOpen">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><circle cx="7" cy="7" r="5.6" stroke="currentColor" stroke-width="1.3" /><path d="M7 4v3.2l2.2 1.3" stroke="currentColor" stroke-width="1.3" /></svg>
      </button>
      <div v-if="sessMenuOpen" class="dd-menu dd-left">
        <div v-for="s in ai.sessions" :key="s.id" class="dd-item"
             :class="{ active: s.id === ai.currentSessionId }" @click="pickSession(s.id)">
          {{ s.title }}<span class="sub">{{ (s.updated_at ?? '').slice(5, 16).replace('T', ' ') }}</span>
        </div>
        <div class="dd-sep" />
        <div class="dd-item" @click="createSession"><AppIcon name="plus" :size="11" /> 新会话</div>
      </div>
    </div>
    <div class="dd" style="margin-left:auto">
      <button class="prov" title="生效中的 Provider(OpenAI 兼容),点击切换"
              @click.stop="provMenuOpen = !provMenuOpen">
        {{ ai.activeProvider?.name ?? '未配置' }} <AppIcon name="chevron" :size="10" />
      </button>
      <div v-if="provMenuOpen" class="dd-menu">
        <div v-for="p in ai.providers" :key="p.id" class="dd-item" :class="{ active: p.is_active }"
             @click="pickProvider(p.id)">
          {{ p.name }}<span class="sub">{{ p.model }}</span><AppIcon v-if="p.is_active" name="check" :size="11" class="dd-check" />
        </div>
        <div class="dd-sep" />
        <div class="dd-item" @click="provMenuOpen = false; ui.settingsVisible = true"><AppIcon name="gear" :size="11" /> 管理 Provider…</div>
      </div>
    </div>
  </div>

  <div class="ai-ctx">
    <span class="lbl">已附加表结构:</span>
    <span v-for="t in ai.ctxTables" :key="t" class="chip">
      {{ t }} <i title="移除" @click="ai.ctxTables.splice(ai.ctxTables.indexOf(t), 1)"><AppIcon name="close" :size="10" /></i>
    </span>
    <input v-model="newTable" class="chip add" style="width:110px;outline:none"
           placeholder="表名,回车添加" @keyup.enter="addCtxTable">
  </div>

  <div ref="msgsEl" class="ai-msgs">
    <div v-if="!ai.messages.length" class="ai-empty">
      用自然语言描述要查的数据<br>
      生成的 SQL 会先放入编辑器,由你确认后执行
    </div>
    <template v-for="(m, i) in ai.messages" :key="i">
      <div v-if="m.role === 'user'" class="msg user">{{ m.text }}</div>
      <div v-else class="msg bot">
        <div class="who">AI 助手{{ m.streaming ? ' · 生成中…' : '' }}</div>
        <p>{{ m.text.replace(/```(?:sql)?/gi, '').replace(/```/g, '') || (m.streaming ? '…' : '') }}</p>
        <div v-if="m.sql" class="sql-block">
          <pre><code>{{ m.sql }}</code></pre>
          <div class="sql-actions">
            <button @click="insertSql(m.sql!, false)">插入到编辑器</button>
            <button class="b-run" @click="insertSql(m.sql!, true)">插入并执行</button>
            <button @click="copySql(m.sql!)">复制</button>
          </div>
        </div>
      </div>
    </template>
  </div>

  <div class="ai-input">
    <div class="box">
      <textarea v-model="question" placeholder="例如:查一下上个月成交额最高的 10 个用户…"
                @keydown.enter.exact.prevent="send" />
      <button class="ai-send" :disabled="ai.generating" title="发送" @click="send">
        <svg width="13" height="13" viewBox="0 0 13 13"><path d="M1.5 6.5h9M7 3l3.5 3.5L7 10" stroke="currentColor" stroke-width="1.6" fill="none" /></svg>
      </button>
    </div>
  </div>
</template>
