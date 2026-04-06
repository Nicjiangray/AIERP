<template>
  <div class="view">
    <div class="view-header">
      <h1 class="view-title">业务数据</h1>
      <span class="mono text-3">schema · business</span>
    </div>
    <div class="table-list">
      <router-link
        v-for="t in store.tables.business"
        :key="t"
        :to="`/business/${t}`"
        class="table-row"
      >
        <span class="table-icon mono">◪</span>
        <span class="table-name mono">{{ t }}</span>
        <span class="arrow">→</span>
      </router-link>
    </div>
  </div>
</template>

<script setup>
import { useAppStore } from '../stores/app'
const store = useAppStore()
</script>

<style scoped>
.view { padding: 32px 36px; max-width: 700px; }
.view-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 32px;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--border);
}
.view-title { font-size: 22px; font-weight: 300; letter-spacing: 0.05em; }
.table-list { display: flex; flex-direction: column; gap: 4px; }
.table-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  text-decoration: none;
  color: var(--text-2);
  transition: all var(--transition);
}
.table-row:hover {
  border-color: var(--border-hi);
  color: var(--text-1);
  background: var(--bg-hover);
}
.table-icon { color: var(--text-3); font-size: 14px; }
.table-name { font-size: 13px; flex: 1; }
.arrow { color: var(--text-3); font-size: 12px; }
.table-row:hover .arrow { color: var(--text-1); }
</style>
