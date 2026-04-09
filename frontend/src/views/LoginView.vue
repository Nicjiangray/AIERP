<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-brand mono">AI · ERP</div>
      <p class="login-hint">登录后可同步会话与记忆</p>
      <n-form @submit.prevent="onSubmit">
        <n-form-item label="登录名" :show-feedback="false">
          <n-input
            v-model:value="loginName"
            placeholder="例如 nicjiang"
            autocomplete="username"
            @keyup.enter="onSubmit"
          />
        </n-form-item>
        <n-form-item label="密码" :show-feedback="false">
          <n-input
            v-model:value="password"
            type="password"
            placeholder="密码"
            autocomplete="current-password"
            show-password-on="click"
            @keyup.enter="onSubmit"
          />
        </n-form-item>
        <p v-if="errorText" class="login-err">{{ errorText }}</p>
        <n-button
          type="primary"
          attr-type="submit"
          block
          :loading="submitting"
          class="login-btn"
        >
          登录
        </n-button>
      </n-form>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { NButton, NForm, NFormItem, NInput } from 'naive-ui'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const emit = defineEmits(['login-success'])
const loginName = ref('')
const password = ref('')
const submitting = ref(false)

const errorText = computed(() => auth.loginError || '')

async function onSubmit() {
  if (submitting.value) return
  submitting.value = true
  try {
    const ok = await auth.login(loginName.value.trim(), password.value)
    if (ok) {
      password.value = ''
      emit('login-success')
    }
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #121212;
  font-family: 'Noto Sans SC', sans-serif;
}

.login-card {
  width: 100%;
  max-width: 380px;
  padding: 32px 28px 28px;
  background: #1e1e1e;
  border: 1px solid #333333;
  border-radius: 8px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.35);
}

.login-brand {
  font-size: 20px;
  letter-spacing: 0.12em;
  color: #e0e0e0;
  margin-bottom: 8px;
}

.login-hint {
  font-size: 13px;
  color: #808080;
  margin-bottom: 24px;
}

.login-err {
  color: #e57373;
  font-size: 13px;
  margin: -4px 0 12px;
}

.login-btn {
  margin-top: 8px;
}

:deep(.n-form-item-label) {
  color: #b0b0b0 !important;
}
</style>
