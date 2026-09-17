/** 全局 UI 状态:弹窗、AI 面板开合、执行触发信号。 */
import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useUiStore = defineStore('ui', () => {
  const connDialogVisible = ref(false)
  const editingConnection = ref<string | null>(null)   // 连接 id,null = 新建
  const settingsVisible = ref(false)
  const aiVisible = ref(false)
  /** 顶栏"执行"按钮 → SqlPane 监听这个自增计数 */
  const executeNonce = ref(0)
  /** AI"插入并执行":目标 Tab 挂载后自动执行一次 */
  const pendingRunTabId = ref<string | null>(null)
  /** 左树当前选中的节点(`${connId}|${path}`):全局唯一,保证高亮互斥 */
  const selectedTreeNode = ref<string | null>(null)

  function openConnDialog(id: string | null = null) {
    editingConnection.value = id
    connDialogVisible.value = true
  }

  function triggerExecute() { executeNonce.value++ }

  return { connDialogVisible, editingConnection, settingsVisible, aiVisible,
           executeNonce, pendingRunTabId, selectedTreeNode, openConnDialog, triggerExecute }
})
