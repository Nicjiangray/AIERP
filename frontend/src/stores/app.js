import { defineStore } from 'pinia'
import { ref } from 'vue'
import { buildExportXlsxUrl } from '../utils/tableExport'

const API = 'http://localhost:8000'

export const useAppStore = defineStore('app', () => {
  const tables = ref({ meta: [], business: [] })
  const nav = ref({ design: [], prep: [], run: [] })
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

  async function fetchNavTables() {
    try {
      const res = await fetch(`${API}/nav/tables`)
      if (!res.ok) throw new Error('nav failed')
      const data = await res.json()
      nav.value = {
        design: data.design || [],
        prep: data.prep || [],
        run: data.run || [],
      }
    } catch (e) {
      console.error('fetchNavTables failed', e)
    }
  }

  async function fetchColumnLabels(tableName) {
    try {
      const res = await fetch(`${API}/table-columns/${encodeURIComponent(tableName)}`)
      if (!res.ok) return { columns: [] }
      return await res.json()
    } catch (e) {
      console.error('fetchColumnLabels failed', e)
      return { columns: [] }
    }
  }

  async function fetchTableData(schema, table, limit = 100, opts = {}) {
    const { filters, q, offset, sortBy, sortOrder } = opts
    try {
      const u = new URL(`${API}/table/${schema}/${table}`)
      u.searchParams.set('limit', String(limit))
      if (offset != null && offset > 0)
        u.searchParams.set('offset', String(offset))
      if (sortBy) u.searchParams.set('sort_by', sortBy)
      if (sortOrder) u.searchParams.set('sort_order', sortOrder)
      if (filters && Object.keys(filters).length)
        u.searchParams.set('filters', JSON.stringify(filters))
      if (q && String(q).trim()) u.searchParams.set('q', String(q).trim())
      const res = await fetch(u.toString())
      return await res.json()
    } catch (e) {
      console.error('fetchTableData failed', e)
      return { rows: [] }
    }
  }

  function _errDetail(data) {
    if (!data || typeof data !== 'object') return '请求失败'
    const d = data.detail
    if (typeof d === 'string') return d
    if (Array.isArray(d)) return d.map((x) => x.msg || JSON.stringify(x)).join('; ')
    return String(d)
  }

  async function tableInsert(schema, table, payload) {
    const res = await fetch(`${API}/table/${schema}/${table}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(_errDetail(data))
    return data
  }

  async function tableUpdate(schema, table, rowId, payload) {
    const res = await fetch(`${API}/table/${schema}/${table}/${rowId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(_errDetail(data))
    return data
  }

  async function tableDelete(schema, table, rowId) {
    const res = await fetch(`${API}/table/${schema}/${table}/${rowId}`, {
      method: 'DELETE',
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(_errDetail(data))
    return data
  }

  /** 简单 xlsx（Excel 2007+），cols 为字段名顺序；同步下载请用 @/utils/tableExport 的 buildExportXlsxUrl */
  async function downloadTableXlsx(schema, table, limit, cols, opts = {}) {
    const res = await fetch(buildExportXlsxUrl(schema, table, limit, cols, opts))
    if (!res.ok) {
      const text = await res.text()
      throw new Error(text || '导出失败')
    }
    return res.blob()
  }

  return {
    tables,
    nav,
    loading,
    fetchTables,
    fetchNavTables,
    fetchColumnLabels,
    fetchTableData,
    tableInsert,
    tableUpdate,
    tableDelete,
    downloadTableXlsx,
    buildExportXlsxUrl,
  }
})
