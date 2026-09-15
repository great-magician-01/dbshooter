<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { useAiStore } from '@/stores/ai'
import { useUiStore } from '@/stores/ui'
import { toast } from '@/utils/toast'

const ui = useUiStore()
const ai = useAiStore()

const sel = ref(0)
const form = reactive({ id: null as string | null, name: '', base_url: '', api_key: '', model: '' })
const testMsg = ref<{ ok: boolean; text: string } | null>(null)
const testing = ref(false)

onMounted(async () => {
  await ai.loadProviders()
  if (ai.providers.length) pick(0)
})

function pick(i: number) {
  sel.value = i
  const p = ai.providers[i]
  form.id = p.id; form.name = p.name; form.base_url = p.base_url
  form.model = p.model; form.api_key = ''
  testMsg.value = null
}

function addNew() {
  sel.value = -1
  form.id = null; form.name = ''; form.base_url = 'https://'; form.api_key = ''; form.model = ''
  testMsg.value = null
}

async function save() {
  try {
    await ai.saveProvider({ id: form.id, name: form.name || '未命名',
      base_url: form.base_url, api_key: form.api_key, model: form.model })
    toast('已保存', 'ok')
    if (sel.value >= ai.providers.length || sel.value < 0) sel.value = 0
    pick(Math.max(sel.value, 0))
  } catch (e: any) { toast(e.message, 'err') }
}

async function activate() {
  if (!form.id) { toast('请先保存', 'err'); return }
  await ai.activateProvider(form.id)
  toast('已设为生效 Provider', 'ok')
}

async function remove() {
  if (!form.id) return
  await ai.deleteProvider(form.id)
  form.id = null
  if (ai.providers.length) { pick(0) } else { addNew() }
}

async function test() {
  testing.value = true
  testMsg.value = null
  try {
    const r = await ai.testProvider({ id: form.id, name: form.name,
      base_url: form.base_url, api_key: form.api_key, model: form.model })
    testMsg.value = { ok: r.ok, text: r.ok ? `✓ ${r.message}` : `✗ ${r.message}` }
  } catch (e: any) {
    testMsg.value = { ok: false, text: `✗ ${e.message}` }
  } finally {
    testing.value = false
  }
}
</script>

<template>
  <div class="modal-mask" @click.self="ui.settingsVisible = false">
    <div class="modal" style="width:680px">
      <div class="modal-head">设置 · AI Provider(OpenAI 兼容,任意时刻仅一个生效)
        <button class="icon-btn" @click="ui.settingsVisible = false">✕</button>
      </div>
      <div class="settings-body">
        <div class="prov-list">
          <div v-for="(p, i) in ai.providers" :key="p.id" class="prov-item"
               :class="{ sel: i === sel }" @click="pick(i)">
            <div class="pn">{{ p.name }}<span v-if="p.is_active" class="on">生效中</span></div>
            <div class="pm">{{ p.model }}</div>
          </div>
          <button class="chip add" @click="addNew">＋ 新增 Provider</button>
        </div>
        <div class="prov-form">
          <div class="form-grid">
            <div class="field"><label>名称</label><input v-model="form.name" placeholder="DeepSeek"></div>
            <div class="field"><label>模型</label><input v-model="form.model" placeholder="deepseek-chat"></div>
            <div class="field full"><label>Base URL(任何 OpenAI 兼容服务)</label><input v-model="form.base_url" placeholder="https://api.deepseek.com/v1"></div>
            <div class="field full"><label>API Key(Fernet 加密存储,本地服务可留空;{{ form.id ? '留空 = 不修改' : '' }})</label>
              <input v-model="form.api_key" type="password"></div>
          </div>
          <div class="prov-actions">
            <button class="btn" :disabled="testing" @click="test">{{ testing ? '测试中…' : '测试' }}</button>
            <span class="test-msg" :class="testMsg?.ok ? 'ok' : 'err'">{{ testMsg?.text }}</span>
            <span style="flex:1" />
            <button v-if="form.id" class="btn" @click="remove">删除</button>
            <button class="btn" @click="activate">设为生效</button>
            <button class="btn primary" @click="save">保存</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
