<template>
  <div class="data-nav">
    <div v-if="loading" class="nav-loading text-3">加载导航…</div>
    <n-collapse v-else :default-expanded-names="['design', 'prep', 'run']" display-directive="show">
      <n-collapse-item v-for="block in blocks" :key="block.key" :title="block.title" :name="block.key">
        <div class="nav-list">
          <router-link
            v-for="item in block.items"
            :key="item.table_name"
            :to="`/${item.schema}/${item.table_name}`"
            class="nav-link"
            :class="{ active: isActive(item) }"
          >
            <span class="nav-cn">{{ item.table_cn || item.table_name }}</span>
            <span class="nav-en mono">{{ item.table_name }}</span>
          </router-link>
        </div>
      </n-collapse-item>
    </n-collapse>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { NCollapse, NCollapseItem } from 'naive-ui'
import { useAppStore } from '../stores/app'
import { navItemInSplitGroup } from '../config/tableSplit'

const store = useAppStore()
const route = useRoute()
const loading = ref(true)

const blocks = computed(() => [
  { key: 'design', title: '系统设计', items: store.nav.design },
  { key: 'prep', title: '系统准备', items: store.nav.prep },
  { key: 'run', title: '系统运行', items: store.nav.run },
])

function isActive(item) {
  const p = route.path
  if (p === `/${item.schema}/${item.table_name}`) return true
  const curTable = route.params?.table
  if (curTable && navItemInSplitGroup(String(curTable), item.table_name)) {
    return true
  }
  return false
}

onMounted(async () => {
  if (
    store.nav.design.length ||
    store.nav.prep.length ||
    store.nav.run.length
  ) {
    loading.value = false
    return
  }
  loading.value = true
  await store.fetchNavTables()
  loading.value = false
})
</script>

<style scoped>
.data-nav {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 6px 10px 12px;
}

.nav-loading {
  padding: 16px;
  font-size: 12px;
}

.nav-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.nav-link {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  padding: 8px 10px;
  border-radius: var(--radius);
  text-decoration: none;
  color: var(--text-2);
  border: 1px solid transparent;
  transition: background var(--transition), border-color var(--transition);
}

.nav-link:hover {
  background: var(--bg-hover);
  color: var(--text-1);
}

.nav-link.active {
  background: var(--bg-active);
  border-color: var(--border-hi);
  color: var(--text-1);
}

.nav-cn {
  font-size: 13px;
  line-height: 1.3;
}

.nav-en {
  font-size: 10px;
  color: var(--text-3);
}

.nav-link.active .nav-en {
  color: var(--text-3);
}

:deep(.n-collapse-item__header) {
  font-size: 11px;
  letter-spacing: 0.08em;
  color: var(--text-3);
  text-transform: uppercase;
  padding: 10px 8px 6px;
}

:deep(.n-collapse-item__content-inner) {
  padding-top: 4px;
}
</style>
