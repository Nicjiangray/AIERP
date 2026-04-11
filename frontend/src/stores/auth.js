import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useAppStore } from './app'

const API = ''

export const useAuthStore = defineStore('auth', () => {
  const currentUser = ref(null)
  const loginError = ref('')

  async function login(login_name, password) {
    loginError.value = ''
    try {
      const res = await fetch(`${API}/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ login_name, password }),
      })
      const data = await res.json().catch(() => ({}))
      if (data.success && data.user) {
        currentUser.value = data.user
        localStorage.setItem('aierp_user_id', String(data.user.id))
        await useAppStore().fetchUserMenu(data.user.id)
        return true
      }
      loginError.value =
        typeof data.error === 'string' ? data.error : '登录失败'
      return false
    } catch (e) {
      loginError.value = e?.message || '网络错误'
      return false
    }
  }

  function logout() {
    currentUser.value = null
    localStorage.removeItem('aierp_user_id')
    useAppStore().clearUserMenu()
  }

  async function restoreSession() {
    const userId = localStorage.getItem('aierp_user_id')
    if (!userId) return
    try {
      const res = await fetch(`${API}/auth/me?user_id=${encodeURIComponent(userId)}`)
      if (res.ok) {
        const data = await res.json()
        const u = data.user ?? data
        currentUser.value = u
        await useAppStore().fetchUserMenu(u?.id)
      } else {
        localStorage.removeItem('aierp_user_id')
        useAppStore().clearUserMenu()
      }
    } catch {
      localStorage.removeItem('aierp_user_id')
      useAppStore().clearUserMenu()
    }
  }

  return {
    currentUser,
    loginError,
    login,
    logout,
    restoreSession,
  }
})
