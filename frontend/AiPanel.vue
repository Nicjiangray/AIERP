<template>
  <aside class="ai-panel">
    <!-- Header -->
    <div class="ai-header">
      <span class="ai-title mono">AI 助手</span>
      <button class="clear-btn" @click="clearChat" title="清空对话">⊘</button>
    </div>

    <!-- 对话列表 -->
    <div class="chat-body" ref="chatBodyRef">
      <!-- 空状态 -->
      <div v-if="messages.length === 0" class="empty-state">
        <div class="empty-icon">◈</div>
        <p>向 AI 提问关于数据库、业务规则或任何 ERP 相关问题</p>
        <div class="suggestions">
          <button
            v-for="s in suggestions"
            :key="s"
            class="suggestion"
            @click="sendMessage(s)"
          >{{ s }}</button>
        </div>
      </div>

      <!-- 消息 -->
      <div
        v-for="(msg, i) in messages"
        :key="i"
        class="message"
        :class="msg.role"
      >
        <div class="msg-meta mono">{{ msg.role === 'user' ? '你' : 'AI' }}</div>
        <div class="msg-body" v-html="renderMarkdown(msg.content)"></div>
      </div>

      <!-- 加载中 -->
      <div v-if="loading" class="message assistant">
        <div class="msg-meta mono">AI</div>
        <div class="msg-body thinking">
          <span></span><span></span><span></span>
        </div>
      </div>
    </div>

    <!-- 输入框 -->
    <div class="ai-input-wrap">
      <textarea
        v-model="inputText"
        class="ai-input"
        placeholder="输入问题... (Enter 发送，Shift+Enter 换行)"
        rows="3"
        @keydown.enter.exact.prevent="handleSend"
        @keydown.shift.enter.exact="() => {}"
        :disabled="loading"
      ></textarea>
      <button class="send-btn" @click="handleSend" :disabled="loading || !inputText.trim()">
        <span>↑</span>
      </button>
    </div>
  </aside>
</template>

<script setup>
import { ref, nextTick } from 'vue'

const messages = ref([])
const inputText = ref('')
const loading = ref(false)
const chatBodyRef = ref(null)

const suggestions = [
  '有哪些业务模块？',
  '如何使用 ',
  '帮我分析业务数据结构',
]

function renderMarkdown(text) {
  // 简单 Markdown 渲染：代码块、加粗、换行
  return text
    .replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\n/g, '<br>')
}

function clearChat() {
  messages.value = []
}

async function scrollToBottom() {
  await nextTick()
  if (chatBodyRef.value) {
    chatBodyRef.value.scrollTop = chatBodyRef.value.scrollHeight
  }
}

async function handleSend() {
  const text = inputText.value.trim()
  if (!text || loading.value) return
  await sendMessage(text)
}

async function sendMessage(text) {
  inputText.value = ''
  messages.value.push({ role: 'user', content: text })
  await scrollToBottom()

  loading.value = true

  try {
    // 构建消息历史（最近 10 条）
    const history = messages.value.slice(-10).map(m => ({
      role: m.role,
      content: m.content
    }))

    const res = await fetch('http://localhost:8000/ai/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: history })
    })

    if (!res.ok) throw new Error('API error')

    const data = await res.json()
    messages.value.push({ role: 'assistant', content: data.content })
  } catch (e) {
    messages.value.push({
      role: 'assistant',
      content: '⚠ 请求失败，请检查后端服务是否启动。'
    })
  } finally {
    loading.value = false
    await scrollToBottom()
  }
}
</script>

<style scoped>
.ai-panel {
  width: var(--ai-w);
  flex-shrink: 0;
  height: 100vh;
  background: var(--bg-panel);
  border-left: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* Header */
.ai-header {
  padding: 18px 20px;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.ai-title {
  font-size: 13px;
  color: var(--text-2);
  letter-spacing: 0.1em;
}
.clear-btn {
  background: none;
  border: none;
  color: var(--text-3);
  cursor: pointer;
  font-size: 16px;
  line-height: 1;
  padding: 2px 4px;
  transition: color var(--transition);
}
.clear-btn:hover { color: var(--text-1); }

/* Chat body */
.chat-body {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* Empty state */
.empty-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  text-align: center;
  padding: 24px 8px;
}
.empty-icon {
  font-size: 28px;
  color: var(--text-3);
}
.empty-state p {
  font-size: 12px;
  color: var(--text-3);
  line-height: 1.7;
}
.suggestions {
  display: flex;
  flex-direction: column;
  gap: 6px;
  width: 100%;
  margin-top: 8px;
}
.suggestion {
  background: var(--bg-hover);
  border: 1px solid var(--border);
  color: var(--text-2);
  font-size: 11px;
  padding: 7px 12px;
  border-radius: var(--radius);
  cursor: pointer;
  text-align: left;
  font-family: var(--font-body);
  transition: all var(--transition);
}
.suggestion:hover {
  border-color: var(--border-hi);
  color: var(--text-1);
  background: var(--bg-active);
}

/* Messages */
.message {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.msg-meta {
  font-size: 10px;
  color: var(--text-3);
  letter-spacing: 0.1em;
}
.message.user .msg-meta { text-align: right; }
.msg-body {
  font-size: 13px;
  line-height: 1.7;
  color: var(--text-1);
}
.message.user .msg-body {
  background: var(--bg-active);
  border: 1px solid var(--border-hi);
  padding: 8px 12px;
  border-radius: var(--radius);
  color: var(--text-1);
  text-align: right;
}
.message.assistant .msg-body {
  color: var(--text-2);
}

/* Inline code */
:deep(code) {
  font-family: var(--font-mono);
  font-size: 12px;
  background: var(--bg-active);
  padding: 1px 5px;
  border-radius: 2px;
  color: var(--accent);
}
:deep(pre) {
  background: var(--bg-active);
  border: 1px solid var(--border);
  padding: 10px 12px;
  border-radius: var(--radius);
  overflow-x: auto;
  margin: 6px 0;
}
:deep(pre code) {
  background: none;
  padding: 0;
}

/* Thinking dots */
.thinking {
  display: flex;
  gap: 4px;
  align-items: center;
  height: 24px;
}
.thinking span {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--text-3);
  animation: pulse 1.2s ease-in-out infinite;
}
.thinking span:nth-child(2) { animation-delay: 0.2s; }
.thinking span:nth-child(3) { animation-delay: 0.4s; }

@keyframes pulse {
  0%, 100% { opacity: 0.2; transform: scale(0.8); }
  50%       { opacity: 1;   transform: scale(1.1); }
}

/* Input */
.ai-input-wrap {
  padding: 12px 16px;
  border-top: 1px solid var(--border);
  display: flex;
  gap: 8px;
  align-items: flex-end;
}
.ai-input {
  flex: 1;
  background: var(--bg-active);
  border: 1px solid var(--border);
  color: var(--text-1);
  font-family: var(--font-body);
  font-size: 13px;
  padding: 8px 12px;
  border-radius: var(--radius);
  resize: none;
  line-height: 1.5;
  transition: border-color var(--transition);
  outline: none;
}
.ai-input:focus {
  border-color: var(--border-hi);
}
.ai-input::placeholder { color: var(--text-3); }
.ai-input:disabled { opacity: 0.5; cursor: not-allowed; }

.send-btn {
  width: 32px;
  height: 32px;
  background: var(--accent);
  color: var(--bg);
  border: none;
  border-radius: var(--radius);
  font-size: 16px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: all var(--transition);
}
.send-btn:hover:not(:disabled) { background: var(--accent-hi); }
.send-btn:disabled { opacity: 0.3; cursor: not-allowed; }
</style>
