<template>
  <nav class="nav-panel">
    <!-- Logo -->
    <div class="logo">
      <span class="logo-mark mono">AI</span>
      <span class="logo-dot">·</span>
      <span class="logo-name">ERP</span>
    </div>

    <!-- 菜单 -->
    <div class="menu-section">
      <div class="section-label mono">核心</div>
      <router-link to="/dashboard" class="menu-item" active-class="active">
        <span class="icon">◈</span>
        <span>仪表盘</span>
      </router-link>
      <router-link to="/ai-rules" class="menu-item" active-class="active">
        <span class="icon">◉</span>
        <span>AI 规则</span>
      </router-link>
    </div>

    <div class="menu-section">
      <div class="section-label mono">数据</div>
      <router-link to="/meta" class="menu-item" active-class="active">
        <span class="icon">◫</span>
        <span>元数据</span>
        <span class="badge">{{ metaCount }}</span>
      </router-link>
      <router-link to="/business" class="menu-item" active-class="active">
        <span class="icon">◪</span>
        <span>业务数据</span>
        <span class="badge">{{ bizCount }}</span>
      </router-link>
    </div>

    <!-- 底部状态 -->
    <div class="nav-footer">
      <div class="status-dot" :class="{ online: apiOk }"></div>
      <span class="mono text-3">{{ apiOk ? 'API · online' : 'API · offline' }}</span>
    </div>
  </nav>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const apiOk = ref(false)

onMounted(async () => {
  try {
    const res = await fetch('http://localhost:8000/')
    apiOk.value = res.ok
    await store.fetchTables()
  } catch {
    apiOk.value = false
  }
})

const metaCount = computed(() => store.tables.meta.length)
const bizCount = computed(() => store.tables.business.length)
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

/* Logo */
.logo {
  padding: 24px 20px 20px;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: baseline;
  gap: 2px;
  letter-spacing: 0.05em;
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

/* Menu */
.menu-section {
  padding: 16px 0 8px;
  border-bottom: 1px solid var(--border);
}
.section-label {
  font-size: 10px;
  color: var(--text-3);
  padding: 0 20px 8px;
  letter-spacing: 0.15em;
  text-transform: uppercase;
}
.menu-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 20px;
  color: var(--text-2);
  text-decoration: none;
  font-size: 13px;
  transition: all var(--transition);
  cursor: pointer;
  position: relative;
}
.menu-item:hover {
  color: var(--text-1);
  background: var(--bg-hover);
}
.menu-item.active {
  color: var(--accent-hi);
  background: var(--bg-active);
}
.menu-item.active::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 2px;
  background: var(--accent-hi);
}
.icon {
  font-size: 14px;
  width: 16px;
  text-align: center;
  flex-shrink: 0;
  color: var(--text-3);
}
.menu-item.active .icon,
.menu-item:hover .icon {
  color: inherit;
}
.badge {
  margin-left: auto;
  font-size: 10px;
  font-family: var(--font-mono);
  color: var(--text-3);
  background: var(--bg-hover);
  padding: 1px 6px;
  border-radius: 2px;
  border: 1px solid var(--border);
}

/* Footer */
.nav-footer {
  margin-top: auto;
  padding: 16px 20px;
  border-top: 1px solid var(--border);
  display: flex;
  align-items: center;
  gap: 8px;
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
