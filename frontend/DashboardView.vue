<template>
  <div class="view">
    <div class="view-header">
      <h1 class="view-title">仪表盘</h1>
      <span class="mono text-3">{{ now }}</span>
    </div>

    <div class="stats-grid">
      <div class="stat-card" v-for="s in stats" :key="s.label">
        <div class="stat-value mono">{{ s.value }}</div>
        <div class="stat-label">{{ s.label }}</div>
      </div>
    </div>

    <div class="section">
      <div class="section-title mono">元数据表</div>
      <div class="table-chips">
        <router-link
          v-for="t in store.tables.meta"
          :key="t"
          :to="`/meta/${t}`"
          class="chip"
        >{{ t }}</router-link>
      </div>
    </div>

    <div class="section">
      <div class="section-title mono">业务数据表</div>
      <div class="table-chips">
        <router-link
          v-for="t in store.tables.business"
          :key="t"
          :to="`/business/${t}`"
          class="chip"
        >{{ t }}</router-link>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const now = new Date().toLocaleDateString('zh-CN', { year:'numeric', month:'long', day:'numeric' })

const stats = computed(() => [
  { label: 'meta 目录表', value: store.tables.meta.length },
  { label: '业务数据表', value: store.tables.business.length },
  { label: 'Schema 总数', value: 2 },
])
</script>

<style scoped>
.view { padding: 32px 36px; max-width: 900px; }
.view-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 32px;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--border);
}
.view-title {
  font-size: 22px;
  font-weight: 300;
  letter-spacing: 0.05em;
  color: var(--text-1);
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-bottom: 40px;
}
.stat-card {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  padding: 20px 24px;
  border-radius: var(--radius);
}
.stat-value {
  font-size: 32px;
  font-weight: 300;
  color: var(--text-1);
  line-height: 1;
  margin-bottom: 6px;
}
.stat-label { font-size: 12px; color: var(--text-3); }

.section { margin-bottom: 32px; }
.section-title {
  font-size: 11px;
  color: var(--text-3);
  letter-spacing: 0.15em;
  text-transform: uppercase;
  margin-bottom: 12px;
}
.table-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.chip {
  font-family: var(--font-mono);
  font-size: 12px;
  padding: 5px 12px;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text-2);
  text-decoration: none;
  transition: all var(--transition);
}
.chip:hover {
  border-color: var(--border-hi);
  color: var(--text-1);
  background: var(--bg-active);
}
</style>
