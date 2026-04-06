<template>
  <n-config-provider :theme="darkTheme" :theme-overrides="themeOverrides">
    <n-message-provider>
      <div class="shell">
        <!-- 左侧导航 -->
        <NavPanel />

        <!-- 中间内容 -->
        <main class="content">
          <router-view v-slot="{ Component }">
            <transition name="fade" mode="out-in">
              <component :is="Component" />
            </transition>
          </router-view>
        </main>

        <!-- 右侧 AI 对话框 -->
        <AiPanel />
      </div>
    </n-message-provider>
  </n-config-provider>
</template>

<script setup>
import { darkTheme } from 'naive-ui'
import NavPanel from './components/NavPanel.vue'
import AiPanel from './components/AiPanel.vue'

// 极简炭灰配色方案 - 已替换
const themeOverrides = {
  common: {
    // 背景色：柔和的炭灰色
    bodyColor: '#121212', 
    cardColor: '#1E1E1E',
    
    // 主题色：带一点点暖调的浅灰，非常克制
    primaryColor: '#BDBDBD', 
    primaryColorHover: '#E0E0E0',
    primaryColorPressed: '#9E9E9E',
    
    // 边框：低调的深灰
    borderColor: '#333333',
    
    // 文字：稍微带一点点暖色倾向的白
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
  }
}
</script>

<style scoped>
.shell {
  display: flex;
  height: 100vh;
  width: 100vw;
  overflow: hidden;
  background: var(--bg);
}

.content {
  flex: 1;
  overflow-y: auto;
  min-width: 0;
  background: var(--bg);
}

/* 路由切换动画 */
.fade-enter-active,
.fade-leave-active {
  transition: opacity 120ms ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
