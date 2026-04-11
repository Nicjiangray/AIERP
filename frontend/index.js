import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    redirect: '/dashboard'
  },
  {
    path: '/dashboard',
    name: 'dashboard',
    component: () => import('../views/DashboardView.vue'),
    meta: { title: '仪表盘' }
  },
  {
    path: '/meta',
    redirect: '/dashboard'
  },
  {
    path: '/meta/:table',
    redirect: '/dashboard'
  },
  {
    path: '/business',
    name: 'business',
    component: () => import('../views/BusinessView.vue'),
    meta: { title: '业务数据' }
  },
  {
    path: '/business/:table',
    name: 'business-table',
    component: () => import('../views/TableView.vue'),
    meta: { title: '表数据' }
  },
  {
    path: '/ai-rules',
    name: 'ai-rules',
    component: () => import('../views/AiRulesView.vue'),
    meta: { title: 'AI 规则' }
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router
