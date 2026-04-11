<template>
  <div class="view dashboard-view">
    <div class="view-header">
      <h1 class="view-title">仪表盘</h1>
      <span class="mono text-3">{{ now }}</span>
    </div>

    <div v-if="loading" class="loading-hint text-2">加载中…</div>
    <div v-else-if="loadError" class="load-err">{{ loadError }}</div>
    <div v-else-if="!authStore.currentUser?.id" class="text-2">请先登录后查看仪表盘。</div>
    <div v-else-if="!cards.length" class="text-2">暂无仪表盘卡片（可由管理员通过 AI 配置）。</div>
    <div v-else class="cards-grid">
      <div v-for="card in cards" :key="card.id" class="card-wrapper">
        <NumberCard v-if="card.card_type === 'number'" :card="card" />
        <BarCard v-else-if="card.card_type === 'bar'" :card="card" />
        <PieCard v-else-if="card.card_type === 'pie'" :card="card" />
        <LineCard v-else-if="card.card_type === 'line'" :card="card" />
        <ListCard v-else-if="card.card_type === 'list'" :card="card" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useAuthStore } from '../stores/auth'
import NumberCard from '../components/dashboard/NumberCard.vue'
import BarCard from '../components/dashboard/BarCard.vue'
import PieCard from '../components/dashboard/PieCard.vue'
import LineCard from '../components/dashboard/LineCard.vue'
import ListCard from '../components/dashboard/ListCard.vue'

const API = ''

const authStore = useAuthStore()
const now = new Date().toLocaleDateString('zh-CN', {
  year: 'numeric',
  month: 'long',
  day: 'numeric',
})

const cards = ref([])
const loading = ref(true)
const loadError = ref('')

onMounted(async () => {
  const uid = authStore.currentUser?.id
  if (!uid) {
    loading.value = false
    return
  }
  loading.value = true
  loadError.value = ''
  try {
    const res = await fetch(`${API}/api/dashboard?user_id=${encodeURIComponent(uid)}`)
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      const d = data?.detail
      const raw = typeof d === 'string' ? d : res.statusText || '加载失败'
      // 保留：旧后端仍可能返回「仪表盘加载失败」，界面统一显示为友好文案
      loadError.value =
        raw === '仪表盘加载失败' ? '您还未创建个人仪表盘。' : raw
      cards.value = []
      return
    }
    cards.value = Array.isArray(data.cards) ? data.cards : []
  } catch (e) {
    loadError.value = e?.message || '网络错误'
    cards.value = []
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.view {
  padding: 32px 36px;
  max-width: 1100px;
}
.view-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 24px;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--border);
}
.view-title {
  font-size: 22px;
  font-weight: 300;
  letter-spacing: 0.05em;
  color: var(--text-1);
}

.loading-hint,
.load-err {
  margin-bottom: 16px;
}
.load-err {
  color: #f44336;
  font-size: 13px;
}

.cards-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
  margin-bottom: 40px;
}
.card-wrapper {
  min-width: 0;
}
</style>
