<template>
  <n-config-provider :theme="darkTheme" :theme-overrides="themeOverrides">
    <LoginView v-if="!auth.currentUser" />
    <div
      v-else
      class="app-shell"
      :style="{ '--nav-w': `${navW}px`, '--ai-w': `${aiW}px` }"
    >
      <NavPanel />
      <div
        class="split-handle split-handle--v split-handle--after-nav"
        role="separator"
        aria-orientation="vertical"
        title="拖动调整左侧栏宽度"
        @mousedown.prevent="startNavResize"
      />
      <main class="main-content">
        <n-config-provider :theme-overrides="mainContentThemeOverrides">
          <RouterView />
        </n-config-provider>
      </main>
      <div
        class="split-handle split-handle--v split-handle--before-ai"
        role="separator"
        aria-orientation="vertical"
        title="拖动调整 AI 面板宽度"
        @mousedown.prevent="startAiResize"
      />
      <AiPanel />
    </div>
  </n-config-provider>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { RouterView } from 'vue-router'
import { NConfigProvider, darkTheme } from 'naive-ui'
import NavPanel from './components/NavPanel.vue'
import AiPanel from './components/AiPanel.vue'
import LoginView from './views/LoginView.vue'
import { useAuthStore } from './stores/auth'

const auth = useAuthStore()

const LS_NAV = 'aierp.layout.navW'
const LS_AI = 'aierp.layout.aiW'
const NAV_MIN = 200
const NAV_MAX = 520
const AI_MIN = 260
const AI_MAX = 720

function readStoredInt(key, fallback) {
  try {
    const v = parseInt(localStorage.getItem(key) || '', 10)
    return Number.isFinite(v) ? v : fallback
  } catch {
    return fallback
  }
}

const navW = ref(Math.min(NAV_MAX, Math.max(NAV_MIN, readStoredInt(LS_NAV, 268))))
const aiW = ref(Math.min(AI_MAX, Math.max(AI_MIN, readStoredInt(LS_AI, 320))))

let drag = null

function startNavResize(e) {
  drag = { kind: 'nav', startX: e.clientX, startNav: navW.value, startAi: aiW.value }
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
  window.addEventListener('mousemove', onResizeMove)
  window.addEventListener('mouseup', endResize, { once: true })
}

function startAiResize(e) {
  drag = { kind: 'ai', startX: e.clientX, startNav: navW.value, startAi: aiW.value }
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
  window.addEventListener('mousemove', onResizeMove)
  window.addEventListener('mouseup', endResize, { once: true })
}

function onResizeMove(e) {
  if (!drag) return
  const dx = e.clientX - drag.startX
  if (drag.kind === 'nav') {
    navW.value = Math.min(NAV_MAX, Math.max(NAV_MIN, Math.round(drag.startNav + dx)))
  } else {
    aiW.value = Math.min(AI_MAX, Math.max(AI_MIN, Math.round(drag.startAi - dx)))
  }
}

function endResize() {
  drag = null
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
  window.removeEventListener('mousemove', onResizeMove)
  try {
    localStorage.setItem(LS_NAV, String(navW.value))
    localStorage.setItem(LS_AI, String(aiW.value))
  } catch {
    /* ignore */
  }
}

onUnmounted(() => {
  window.removeEventListener('mousemove', onResizeMove)
})

onMounted(() => {
  auth.restoreSession()
})

/** 全局暗色（侧栏 / AI 面板）；勿在此写 tertiary 背景，否则中间浅色页按钮会全黑 */
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
  Scrollbar: {
    color: '#333333',
    colorHover: '#444444',
  },
}

/** 仅 RouterView：浅色背景上的 tertiary 按钮（与 main.css 变量一致） */
const mainContentThemeOverrides = {
  Button: {
    colorTertiary: '#f3f4f6',
    colorTertiaryHover: '#e8eaed',
    colorTertiaryPressed: '#dce0e6',
    textColorTertiary: '#374151',
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

.split-handle--v {
  width: 6px;
  flex-shrink: 0;
  cursor: col-resize;
  align-self: stretch;
  background: transparent;
  position: relative;
  z-index: 2;
}
.split-handle--v:hover,
.split-handle--v:active {
  background: color-mix(in srgb, var(--border-hi) 85%, transparent);
}
.split-handle--after-nav {
  margin-left: -3px;
  margin-right: -3px;
}
.split-handle--before-ai {
  margin-left: -3px;
  margin-right: -3px;
}
</style>
