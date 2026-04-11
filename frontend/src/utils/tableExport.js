import { API_BASE } from '../config/api'

/** 与后端 GET /export/xlsx/... 查询参数一致 */
export function buildExportXlsxUrl(schema, table, limit, cols, opts = {}) {
  const { filters, q, sortBy, sortOrder } = opts
  const params = new URLSearchParams({ limit: String(limit) })
  if (cols?.length) params.set('cols', cols.join(','))
  if (sortBy) params.set('sort_by', sortBy)
  if (sortOrder) params.set('sort_order', sortOrder)
  if (filters && Object.keys(filters).length)
    params.set('filters', JSON.stringify(filters))
  if (q && String(q).trim()) params.set('q', String(q).trim())
  return `${API_BASE}/export/xlsx/${encodeURIComponent(schema)}/${encodeURIComponent(table)}?${params}`
}
