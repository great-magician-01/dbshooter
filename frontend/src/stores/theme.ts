/** 主题:dark / light,settings 表 + localStorage 双写。 */
import { defineStore } from 'pinia'
import { ref } from 'vue'

import { get, post } from '@/api/http'

export const useThemeStore = defineStore('theme', () => {
  const mode = ref<'dark' | 'light'>(
    (localStorage.getItem('ds-theme') as 'dark' | 'light') || 'dark')

  function apply() {
    document.documentElement.dataset.theme = mode.value
    localStorage.setItem('ds-theme', mode.value)
  }

  function toggle() {
    mode.value = mode.value === 'dark' ? 'light' : 'dark'
    apply()
    post('/api/settings/save', { values: { theme: mode.value } }).catch(() => {})
  }

  async function init() {
    apply()
    try {
      const { values } = await get<{ values: Record<string, string> }>('/api/settings')
      if (values.theme === 'light' || values.theme === 'dark') {
        mode.value = values.theme
        apply()
      }
    } catch { /* 后端未就绪时用本地值 */ }
  }

  return { mode, toggle, init }
})
