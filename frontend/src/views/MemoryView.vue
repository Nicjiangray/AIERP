<template>
  <div class="view memory-view">
    <div class="view-header">
      <h1 class="view-title">记忆管理</h1>
      <span class="mono text-3">core + longterm</span>
    </div>

    <p class="memory-intro">
      以下是 AI 助手从工作中积累的记忆和经验。核心经验会影响助手的行为模式，你可以修正或补充。
    </p>

    <div v-if="!uid" class="hint text-3">请先登录后查看记忆。</div>

    <template v-else>
      <div v-if="loading" class="loading-wrap">
        <n-spin size="small" />
      </div>

      <div v-else class="memory-sections">
        <section class="mem-block">
          <div class="mem-block-head">
            <h2 class="mem-h2">核心经验</h2>
            <button type="button" class="btn-add" @click="addRow('core')">添加经验</button>
          </div>
          <div v-if="errCore" class="err">{{ errCore }}</div>
          <div v-for="row in coreItems" :key="row.id" class="mem-card">
            <template v-if="editingId === row.id">
              <textarea v-model="editDraft" class="mem-textarea" rows="3" />
              <div class="mem-actions">
                <button type="button" class="btn-save" @click="saveEdit(row)">保存</button>
                <button type="button" class="btn-cancel" @click="cancelEdit">取消</button>
              </div>
            </template>
            <template v-else>
              <div class="mem-body">{{ row.content }}</div>
              <div class="mem-actions">
                <button type="button" class="btn-edit" @click="startEdit(row)">编辑</button>
                <button type="button" class="btn-del" @click="removeRow(row)">删除</button>
              </div>
            </template>
          </div>
        </section>

        <section class="mem-block">
          <div class="mem-block-head">
            <h2 class="mem-h2">业务记忆</h2>
            <button type="button" class="btn-add" @click="addRow('longterm')">添加</button>
          </div>
          <div v-if="errLt" class="err">{{ errLt }}</div>
          <div v-for="row in longtermItems" :key="row.id" class="mem-card">
            <template v-if="editingId === row.id">
              <textarea v-model="editDraft" class="mem-textarea" rows="6" />
              <div class="mem-actions">
                <button type="button" class="btn-save" @click="saveEdit(row)">保存</button>
                <button type="button" class="btn-cancel" @click="cancelEdit">取消</button>
              </div>
            </template>
            <template v-else>
              <div class="mem-body mono">{{ row.content }}</div>
              <div class="mem-actions">
                <button type="button" class="btn-edit" @click="startEdit(row)">编辑</button>
                <button type="button" class="btn-del" @click="removeRow(row)">删除</button>
              </div>
            </template>
          </div>
        </section>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { NSpin } from 'naive-ui'
import { useAuthStore } from '../stores/auth'

const API = ''
const auth = useAuthStore()

const uid = computed(() => auth.currentUser?.id ?? null)
const loading = ref(false)
const coreItems = ref([])
const longtermItems = ref([])
const errCore = ref('')
const errLt = ref('')
const editingId = ref(null)
const editDraft = ref('')

async function loadAll() {
  if (!uid.value) return
  loading.value = true
  errCore.value = ''
  errLt.value = ''
  try {
    const [c, l] = await Promise.all([
      fetch(`${API}/ai/memory?user_id=${uid.value}&scope=core`).then((r) => r.json()),
      fetch(`${API}/ai/memory?user_id=${uid.value}&scope=longterm`).then((r) => r.json()),
    ])
    coreItems.value = c.items || []
    longtermItems.value = l.items || []
  } catch (e) {
    errCore.value = String(e?.message || e)
  } finally {
    loading.value = false
  }
}

watch(uid, () => loadAll(), { immediate: true })
onMounted(() => loadAll())

function startEdit(row) {
  editingId.value = row.id
  editDraft.value = row.content || ''
}

function cancelEdit() {
  editingId.value = null
  editDraft.value = ''
}

async function saveEdit(row) {
  const content = (editDraft.value || '').trim()
  if (!content) return
  const res = await fetch(`${API}/ai/memory/${row.id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: uid.value, content }),
  })
  if (!res.ok) {
    const d = await res.json().catch(() => ({}))
    alert(d.detail || '保存失败')
    return
  }
  cancelEdit()
  await loadAll()
}

async function removeRow(row) {
  if (!confirm('确定删除这条记忆？')) return
  const res = await fetch(`${API}/ai/memory/${row.id}?user_id=${uid.value}`, {
    method: 'DELETE',
  })
  if (!res.ok) {
    const d = await res.json().catch(() => ({}))
    alert(d.detail || '删除失败')
    return
  }
  if (editingId.value === row.id) cancelEdit()
  await loadAll()
}

async function addRow(scope) {
  const content = window.prompt(scope === 'core' ? '请输入核心经验（一行）' : '请输入业务记忆正文')
  if (content == null || !String(content).trim()) return
  const res = await fetch(`${API}/ai/memory`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      user_id: uid.value,
      scope,
      content: String(content).trim(),
    }),
  })
  if (!res.ok) {
    const d = await res.json().catch(() => ({}))
    alert(d.detail || '添加失败')
    return
  }
  await loadAll()
}
</script>

<style scoped>
.memory-view {
  padding: 32px 36px;
  max-width: 880px;
  overflow-y: auto;
  height: 100%;
}
.view-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 16px;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--border);
}
.view-title {
  font-size: 22px;
  font-weight: 300;
  letter-spacing: 0.05em;
}
.memory-intro {
  font-size: 13px;
  color: var(--text-2);
  line-height: 1.6;
  margin-bottom: 28px;
}
.hint {
  padding: 24px 0;
}
.loading-wrap {
  display: flex;
  justify-content: center;
  padding: 40px;
}
.memory-sections {
  display: flex;
  flex-direction: column;
  gap: 36px;
}
.mem-block-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}
.mem-h2 {
  font-size: 15px;
  font-weight: 500;
  letter-spacing: 0.06em;
  color: var(--text-1);
}
.mem-card {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 14px 16px;
  margin-bottom: 10px;
}
.mem-body {
  font-size: 13px;
  line-height: 1.65;
  color: var(--text-2);
  white-space: pre-wrap;
  word-break: break-word;
}
.mem-actions {
  display: flex;
  gap: 10px;
  margin-top: 10px;
}
.mem-textarea {
  width: 100%;
  box-sizing: border-box;
  background: var(--bg-active);
  border: 1px solid var(--border-hi);
  border-radius: var(--radius);
  color: var(--text-1);
  font-size: 13px;
  padding: 10px 12px;
  font-family: inherit;
  resize: vertical;
}
.btn-add,
.btn-edit,
.btn-save,
.btn-cancel,
.btn-del {
  font-size: 12px;
  padding: 6px 12px;
  border-radius: var(--radius);
  cursor: pointer;
  border: 1px solid var(--border);
  background: var(--bg-active);
  color: var(--text-2);
}
.btn-add:hover,
.btn-edit:hover {
  border-color: var(--border-hi);
  color: var(--text-1);
}
.btn-save {
  border-color: var(--border-hi);
  color: var(--accent-hi);
}
.btn-del {
  color: #c99;
}
.err {
  color: #c99;
  font-size: 12px;
  margin-bottom: 8px;
}
</style>
