<template>
  <div class="view">
    <!-- Header -->
    <div class="view-header">
      <div class="breadcrumb">
        <router-link :to="`/${schema}`" class="crumb">{{ schema }}</router-link>
        <span class="sep mono">/</span>
        <span class="crumb active mono">{{ tableName }}</span>
      </div>
      <span class="row-count mono text-3" v-if="!loading">{{ rows.length }} 行</span>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="loading-wrap">
      <n-spin size="small" />
      <span class="text-3">加载中...</span>
    </div>

    <!-- 数据表 -->
    <div v-else-if="rows.length > 0" class="table-wrap">
      <n-data-table
        :columns="columns"
        :data="rows"
        :max-height="'calc(100vh - 140px)'"
        :bordered="false"
        :single-line="false"
        size="small"
        striped
        virtual-scroll
      />
    </div>

    <!-- 空表 -->
    <div v-else class="empty">
      <span class="mono text-3">— 该表暂无数据 —</span>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useAppStore } from '../stores/app'
import { NDataTable, NSpin } from 'naive-ui'
import { h } from 'vue'

const route = useRoute()
const store = useAppStore()

const schema = computed(() => 'business')
const tableName = computed(() => route.params.table)

const rows = ref([])
const loading = ref(false)

const columns = computed(() =>
  Object.keys(rows.value[0] || {}).map(key => ({
    title: key,
    key,
    sorter: 'default',
    ellipsis: { tooltip: true },
    width: 140,
    render: (row) => {
      const val = row[key]
      if (val === null || val === undefined) {
        return h('span', { style: 'color: var(--text-3); font-style: italic;' }, 'null')
      }
      return h('span', { class: 'cell-val' }, String(val))
    }
  }))
)

async function load() {
  loading.value = true
  const data = await store.fetchTableData(schema.value, tableName.value, 200)
  rows.value = data.rows || []
  loading.value = false
}

onMounted(load)
watch([schema, tableName], load)
</script>

<style scoped>
.view { padding: 24px 28px; height: 100vh; display: flex; flex-direction: column; overflow: hidden; }

.view-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.breadcrumb { display: flex; align-items: center; gap: 8px; }
.crumb {
  font-size: 14px;
  color: var(--text-3);
  text-decoration: none;
  transition: color var(--transition);
}
.crumb:hover { color: var(--text-2); }
.crumb.active { color: var(--text-1); font-size: 16px; font-weight: 400; }
.sep { color: var(--text-3); }
.row-count { font-size: 12px; }

.loading-wrap {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: var(--text-3);
  font-size: 13px;
}

.table-wrap { flex: 1; overflow: hidden; }

.empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
}

/* Naive UI table overrides */
:deep(.n-data-table) {
  background: transparent;
}
:deep(.n-data-table-thead .n-data-table-th) {
  background: var(--bg-panel);
  border-bottom: 1px solid var(--border);
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-3);
  letter-spacing: 0.05em;
  text-transform: uppercase;
}
:deep(.n-data-table-td) {
  border-bottom: 1px solid var(--border);
  font-size: 12px;
}
:deep(.n-data-table-tr:hover .n-data-table-td) {
  background: var(--bg-hover);
}
:deep(.n-data-table-tr--striped .n-data-table-td) {
  background: var(--bg-panel);
}
:deep(.n-data-table-tr--striped:hover .n-data-table-td) {
  background: var(--bg-hover);
}

.cell-val {
  font-family: var(--font-mono);
  font-size: 12px;
}
</style>
