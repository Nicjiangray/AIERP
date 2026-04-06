<template>
  <nav class="nav-panel">
    <div class="logo">
      <span class="logo-mark mono">AI</span>
      <span class="logo-dot">·</span>
      <span class="logo-name">ERP</span>
    </div>

    <div class="nav-quick">
      <router-link to="/dashboard" class="quick-link" active-class="active">仪表盘</router-link>
      <router-link to="/ai-rules" class="quick-link" active-class="active">AI 规则</router-link>
    </div>

    <div class="nav-data-wrap">
      <DataNavPanel />
    </div>

    <div class="nav-footer">
      <div class="status-dot" :class="{ online: apiOk }"></div>
      <span class="mono text-3">{{ apiOk ? 'API · online' : 'API · offline' }}</span>
    </div>
  </nav>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useAppStore } from '../stores/app'
import DataNavPanel from './DataNavPanel.vue'

const store = useAppStore()
const apiOk = ref(false)

onMounted(async () => {
  try {
    const res = await fetch('http://localhost:8000/')
    apiOk.value = res.ok
    await store.fetchTables()
    await store.fetchNavTables()
  } catch {
    apiOk.value = false
  }
})
</script>

<style scoped>
.nav-panel {
  width: var(--nav-w);
  flex-shrink: 0;
  height: 100vh;
  background: var(--bg-panel);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.logo {
  padding: 20px 16px 14px;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: baseline;
  gap: 2px;
  letter-spacing: 0.05em;
  flex-shrink: 0;
}
.logo-mark {
  font-size: 18px;
  font-weight: 500;
  color: var(--accent-hi);
}
.logo-dot {
  color: var(--text-3);
  font-size: 18px;
}
.logo-name {
  font-size: 16px;
  font-weight: 300;
  color: var(--text-2);
  letter-spacing: 0.15em;
}

.nav-quick {
  display: flex;
  gap: 8px;
  padding: 10px 12px 8px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.quick-link {
  font-size: 12px;
  color: var(--text-3);
  text-decoration: none;
  padding: 4px 8px;
  border-radius: var(--radius);
  transition: color var(--transition), background var(--transition);
}
.quick-link:hover {
  color: var(--text-1);
  background: var(--bg-hover);
}
.quick-link.active {
  color: var(--accent-hi);
  background: var(--bg-active);
}

.nav-data-wrap {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.nav-footer {
  padding: 12px 16px;
  border-top: 1px solid var(--border);
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-3);
  flex-shrink: 0;
}
.status-dot.online {
  background: var(--success);
  box-shadow: 0 0 6px var(--success);
}
.nav-footer span {
  font-size: 11px;
}
</style>
