/**
 * 后端 API 根地址（无尾部斜杠）。
 * 由 Vite 注入：.env.development / .env.production 中的 VITE_API_BASE。
 * - 开发：默认 http://localhost:8000，直连本机 FastAPI
 * - 生产构建：空字符串，与页面同域，由 Nginx 反代到后端
 */
export const API_BASE = (import.meta.env.VITE_API_BASE ?? '').replace(/\/$/, '')

/** 供 `new URL(path, base)`：有 API_BASE 时用其作为 base，否则用当前页 origin */
export function apiUrlBase() {
  if (API_BASE) return API_BASE
  if (typeof window !== 'undefined' && window.location?.origin) {
    return window.location.origin
  }
  return ''
}

/** 健康检查：开发指后端根；生产同域用 `/` */
export function apiHealthCheckUrl() {
  return API_BASE ? `${API_BASE}/` : '/'
}
