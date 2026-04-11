import { defineStore } from 'pinia'
import { ref } from 'vue'
import { buildExportXlsxUrl } from '../utils/tableExport'

const API = ''

export const useAppStore = defineStore('app', () => {
  const tables = ref({ meta: [], business: [] })
  const nav = ref({ design: [], prep: [], run: [] })
  const loading = ref(false)
  /** table_name -> 中文表名（来自 meta_tables 或 null）；用对象便于覆盖更新 */
  const tableCnByName = ref({})

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
      const next = { ...tableCnByName.value }
      for (const block of ['design', 'prep', 'run']) {
        for (const item of nav.value[block] || []) {
          if (item.table_name && item.table_cn) {
            next[item.table_name] = item.table_cn
          }
        }
      }
      tableCnByName.value = next
    } catch (e) {
      console.error('fetchNavTables failed', e)
    }
  }

  /** 权限驱动导航（与 /nav/tables 结构相同）；未登录勿调用 */
  async function fetchUserMenu(userId) {
    if (userId == null || userId === '') {
      clearUserMenu()
      return
    }
    try {
      const res = await fetch(
        `${API}/api/user-menu?user_id=${encodeURIComponent(userId)}`
      )
      if (!res.ok) throw new Error('user-menu failed')
      const data = await res.json()
      nav.value = {
        design: data.design || [],
        prep: data.prep || [],
        run: data.run || [],
      }
      const next = { ...tableCnByName.value }
      for (const block of ['design', 'prep', 'run']) {
        for (const item of nav.value[block] || []) {
          if (item.table_name && item.table_cn) {
            next[item.table_name] = item.table_cn
          }
        }
      }
      tableCnByName.value = next
    } catch (e) {
      console.error('fetchUserMenu failed', e)
    }
  }

  function clearUserMenu() {
    nav.value = { design: [], prep: [], run: [] }
  }

  /** 解析中文表名：先导航缓存，再请求 meta_tables */
  async function ensureTableCn(tableName) {
    const name = String(tableName || '')
    if (!name) return null
    if (tableCnByName.value[name] !== undefined) {
      return tableCnByName.value[name]
    }
    try {
      const res = await fetch(
        `${API}/meta/table-cn/${encodeURIComponent(name)}`
      )
      if (!res.ok) {
        tableCnByName.value = { ...tableCnByName.value, [name]: null }
        return null
      }
      const data = await res.json()
      const cn = data.table_cn || null
      tableCnByName.value = { ...tableCnByName.value, [name]: cn }
      return cn
    } catch {
      tableCnByName.value = { ...tableCnByName.value, [name]: null }
      return null
    }
  }

  function resolveTableCn(tableName) {
    const name = String(tableName || '')
    if (!name) return ''
    const hit = tableCnByName.value[name]
    if (hit) return hit
    return ''
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
    tableCnByName,
    ensureTableCn,
    resolveTableCn,
    loading,
    fetchTables,
    fetchNavTables,
    fetchUserMenu,
    clearUserMenu,
    fetchColumnLabels,
    fetchTableData,
    tableInsert,
    tableUpdate,
    tableDelete,
    downloadTableXlsx,
    buildExportXlsxUrl,
  }
})
