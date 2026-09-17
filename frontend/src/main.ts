import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import { toast } from '@/utils/toast'
import './styles/main.css'

const app = createApp(App)

// 兜底:组件渲染/生命周期里抛出的异常也要有声音,否则用户只看到白屏或错乱的界面
app.config.errorHandler = (err, _instance, info) => {
  console.error('[dbshooter] 未捕获异常:', err, info)
  const msg = err instanceof Error ? err.message : String(err)
  toast(`页面异常:${msg}`, 'err')
}

app.use(createPinia()).mount('#app')
