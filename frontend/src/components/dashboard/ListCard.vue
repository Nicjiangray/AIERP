<template>
  <div class="list-card">
    <div class="card-title">{{ card.title }}</div>
    <div v-if="card.description" class="card-desc text-2">{{ card.description }}</div>
    <div v-if="!card.error && card.data?.length" class="table-wrap">
      <table class="plain">
        <thead>
          <tr>
            <th v-for="col in columns" :key="col">{{ col }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, i) in card.data" :key="i">
            <td v-for="col in columns" :key="col">{{ formatCell(row[col]) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-else-if="card.error" class="card-error">{{ card.error }}</div>
    <div v-else class="card-empty text-2">暂无数据</div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ card: { type: Object, required: true } })

const columns = computed(() => {
  const rows = props.card.data || []
  return rows.length ? Object.keys(rows[0]) : []
})

function formatCell(v) {
  if (v == null) return '—'
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}
</script>

<style scoped>
.list-card {
  background: var(--bg-panel);
  border-radius: var(--radius);
  padding: 16px;
  border: 1px solid var(--border);
}
.card-title {
  font-size: 13px;
  color: var(--text-3);
  margin-bottom: 8px;
}
.card-desc {
  font-size: 12px;
  margin-bottom: 10px;
}
.table-wrap {
  overflow-x: auto;
}
.plain {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.plain th {
  text-align: left;
  padding: 8px;
  border-bottom: 2px solid var(--border);
  color: var(--text-2);
  white-space: nowrap;
}
.plain td {
  padding: 8px;
  border-bottom: 1px solid var(--border);
  color: var(--text-1);
}
.card-error {
  font-size: 12px;
  color: #f44336;
}
.card-empty {
  padding: 24px;
  text-align: center;
}
</style>
