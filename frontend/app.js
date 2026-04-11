import { defineStore } from 'pinia'
import { ref } from 'vue'

const API = ''

export const useAppStore = defineStore('app', () => {
  const tables = ref({ meta: [], business: [] })
  const loading = ref(false)

  async function fetchTables() {
    try {
      const res = await fetch(`${API}/tables`)
      const data = await res.json()
      tables.value.meta = data.filter(t => t.schema === 'meta').map(t => t.table)
      tables.value.business = data.filter(t => t.schema === 'business').map(t => t.table)
    } catch (e) {
      console.error('fetchTables failed', e)
    }
  }

  async function fetchTableData(schema, table, limit = 100) {
    loading.value = true
    try {
      const res = await fetch(`${API}/table/${schema}/${table}?limit=${limit}`)
      return await res.json()
    } catch (e) {
      console.error('fetchTableData failed', e)
      return { columns: [], rows: [] }
    } finally {
      loading.value = false
    }
  }

  return { tables, loading, fetchTables, fetchTableData }
})
