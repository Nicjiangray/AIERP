<template>
  <div
    class="number-card"
    :style="{ borderLeftColor: config.color || 'var(--accent-hi)' }"
  >
    <div class="card-title">{{ card.title }}</div>
    <div class="card-value" :style="{ color: config.color || 'var(--accent-hi)' }">
      {{ config.prefix || '' }}{{ formatNumber(value) }}{{ config.unit ? ' ' + config.unit : '' }}
    </div>
    <div v-if="card.description" class="card-desc text-2">{{ card.description }}</div>
    <div v-if="card.error" class="card-error">{{ card.error }}</div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ card: { type: Object, required: true } })

const config = computed(() => props.card.card_config || {})

const value = computed(() => {
  const rows = props.card.data || []
  if (!rows.length) return 0
  const v = rows[0].value
  if (v == null) return 0
  return v
})

function formatNumber(n) {
  if (n == null) return '0'
  const num = Number(n)
  if (Number.isNaN(num)) return String(n)
  return num.toLocaleString('zh-CN')
}
</script>

<style scoped>
.number-card {
  background: var(--bg-panel);
  border-radius: var(--radius);
  padding: 20px;
  border: 1px solid var(--border);
  border-left-width: 4px;
  border-left-style: solid;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
}
.card-title {
  font-size: 13px;
  color: var(--text-3);
  margin-bottom: 8px;
}
.card-value {
  font-size: 28px;
  font-weight: 500;
  font-family: var(--font-mono, monospace);
}
.card-desc {
  font-size: 12px;
  margin-top: 8px;
}
.card-error {
  font-size: 12px;
  color: #f44336;
  margin-top: 8px;
}
</style>
