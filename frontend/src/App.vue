<template>
  <n-config-provider :theme="darkTheme" :theme-overrides="themeOverrides">
    <LoginView v-if="!auth.currentUser" />
    <div v-else class="app-shell">
      <NavPanel />
      <main class="main-content">
        <RouterView />
      </main>
      <AiPanel />
    </div>
  </n-config-provider>
</template>

<script setup>
import { onMounted } from 'vue'
import { RouterView } from 'vue-router'
import { NConfigProvider, darkTheme } from 'naive-ui'
import NavPanel from './components/NavPanel.vue'
import AiPanel from './components/AiPanel.vue'
import LoginView from './views/LoginView.vue'
import { useAuthStore } from './stores/auth'

const auth = useAuthStore()

onMounted(() => {
  auth.restoreSession()
})

/** 与 naive-ui 表格/组件一致的全局主题（AiPanel 内 n-data-table 依赖此上下文） */
const themeOverrides = {
  common: {
    bodyColor: '#121212',
    cardColor: '#1E1E1E',
    primaryColor: '#BDBDBD',
    primaryColorHover: '#E0E0E0',
    primaryColorPressed: '#9E9E9E',
    borderColor: '#333333',
    textColorBase: '#E0E0E0',
    textColor1: '#B0B0B0',
    textColor2: '#808080',
    textColor3: '#505050',
    borderRadius: '4px',
    fontFamily: "'Noto Sans SC', sans-serif",
  },
  Input: {
    color: '#1E1E1E',
    colorFocus: '#252525',
    border: '1px solid #333333',
    borderFocus: '1px solid #555555',
  },
  Button: {
    colorTertiary: '#252525',
    colorTertiaryHover: '#2C2C2C',
  },
  Scrollbar: {
    color: '#333333',
    colorHover: '#444444',
  },
}
</script>

<style scoped>
.app-shell {
  display: flex;
  width: 100%;
  height: 100vh;
  overflow: hidden;
}

.main-content {
  flex: 1;
  min-width: 0;
  overflow: hidden;
}
</style>
