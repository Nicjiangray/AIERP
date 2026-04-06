<template>
  <div class="view">
    <div class="view-header">
      <h1 class="view-title">AI 规则</h1>
      <span class="mono text-3">ai_rules · {{ rows.length }} 条</span>
    </div>

    <div v-if="loading" class="loading-wrap">
      <n-spin size="small" />
    </div>

    <div v-else class="rules-list">
      <div v-for="row in rows" :key="row.id" class="rule-card">
        <div class="rule-top">
          <span class="rule-id mono">#{{ row.id }}</span>
          <span class="rule-category" v-if="row.category">{{ row.category }}</span>
          <span class="rule-priority mono" :class="priorityClass(row.priority)">
            P{{ row.priority ?? '—' }}
          </span>
        </div>
        <div class="rule-name">{{ row.rule_name || row.name }}</div>
        <div class="rule-desc text-2" v-if="row.description">{{ row.description }}</div>
        <div class="rule-content mono" v-if="row.rule_content || row.content">
          {{ row.rule_content || row.content }}
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useAppStore } from '../stores/app'
import { NSpin } from 'naive-ui'

const store = useAppStore()
const rows = ref([])
const loading = ref(false)

onMounted(async () => {
  loading.value = true
  const data = await store.fetchTableData('meta', 'ai_rules', 100)
  rows.value = data.rows || []
  loading.value = false
})

function priorityClass(p) {
  if (!p) return ''
  if (p <= 3) return 'hi'
  if (p <= 6) return 'mid'
  return 'lo'
}
</script>

<style scoped>
.view { padding: 32px 36px; max-width: 800px; }
.view-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 32px;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--border);
}
.view-title { font-size: 22px; font-weight: 300; letter-spacing: 0.05em; }

.loading-wrap {
  display: flex;
  justify-content: center;
  padding: 60px;
}

.rules-list { display: flex; flex-direction: column; gap: 10px; }

.rule-card {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  transition: border-color var(--transition);
}
.rule-card:hover { border-color: var(--border-hi); }

.rule-top {
  display: flex;
  align-items: center;
  gap: 10px;
}
.rule-id {
  font-size: 11px;
  color: var(--text-3);
}
.rule-category {
  font-size: 11px;
  background: var(--bg-active);
  border: 1px solid var(--border);
  color: var(--text-2);
  padding: 1px 8px;
  border-radius: 2px;
}
.rule-priority {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 2px;
  border: 1px solid var(--border);
  margin-left: auto;
}
.rule-priority.hi  { color: #e74c3c; border-color: #e74c3c44; }
.rule-priority.mid { color: #f39c12; border-color: #f39c1244; }
.rule-priority.lo  { color: var(--text-3); }

.rule-name {
  font-size: 14px;
  color: var(--text-1);
  font-weight: 400;
}
.rule-desc {
  font-size: 12px;
  line-height: 1.6;
}
.rule-content {
  font-size: 11px;
  color: var(--text-3);
  background: var(--bg-active);
  padding: 8px 10px;
  border-radius: var(--radius);
  border: 1px solid var(--border);
  line-height: 1.6;
  margin-top: 4px;
  white-space: pre-wrap;
}
</style>
