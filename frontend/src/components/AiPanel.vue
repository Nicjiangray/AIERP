<template>
  <aside class="ai-panel">
    <!-- Header -->
    <div class="ai-header">
      <span class="ai-title mono">AI 助手</span>
      <div class="ai-header-actions">
        <button
          v-if="loading"
          type="button"
          class="stop-btn"
          @click="stopGeneration"
          title="终止自动执行（断开 SSE，多步任务可随时中断）"
        >
          终止
        </button>
        <button class="clear-btn" type="button" @click="clearChat" title="清空对话">⊘</button>
      </div>
    </div>

    <!-- 对话列表 -->
    <div class="chat-body" ref="chatBodyRef">
      <!-- 空状态：本地欢迎语（不入库），首条发送后会写入 messages 保留在列表中 -->
      <div v-if="messages.length === 0" class="empty-state">
        <div class="message assistant msg-local-welcome empty-welcome">
          <div class="msg-meta mono">AI</div>
          <div class="msg-body" v-html="renderMarkdown(localWelcomeText)"></div>
        </div>
      </div>

      <!-- 消息 -->
      <div
        v-for="(msg, i) in messages"
        :key="i"
        class="message"
        :class="[
          msg.role,
          {
            'confirm-exec-error': msg.confirmExecError,
            'msg-local-welcome': msg.isLocalWelcome,
          },
        ]"
      >
        <!-- 新建实体：待确认写入（独立卡片，非普通气泡） -->
        <template v-if="msg.role === 'assistant' && msg.confirmRequired">
          <div class="msg-meta mono">AI</div>
          <div class="msg-confirm-card">
            <div class="msg-confirm-title">请确认以下操作</div>
            <div
              v-if="msg.explanation"
              class="msg-confirm-explanation"
              v-html="renderMarkdown(msg.explanation)"
            ></div>
            <div class="msg-table-scroll msg-confirm-table-wrap">
              <template v-if="isWritesPreview(msg.preview)">
                <div
                  v-for="(w, wi) in msg.preview.writes"
                  :key="'w' + wi"
                  class="msg-confirm-write-seg"
                >
                  <div class="msg-confirm-subtitle">{{ writeSegmentTitle(w) }}</div>
                  <p v-if="w.label" class="mono writes-label">{{ w.label }}</p>
                  <table
                    v-if="w.fields && typeof w.fields === 'object'"
                    class="msg-plain-table msg-confirm-preview-table"
                  >
                    <thead>
                      <tr>
                        <th>字段</th>
                        <th>值</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="(ent, ri) in Object.entries(w.fields)" :key="'f' + ri">
                        <td class="mono">{{ ent[0] }}</td>
                        <td>{{ formatPreviewValue(ent[1]) }}</td>
                      </tr>
                    </tbody>
                  </table>
                  <table
                    v-else-if="Array.isArray(w.rows) && w.rows.length"
                    class="msg-plain-table msg-confirm-preview-table"
                  >
                    <thead>
                      <tr>
                        <th
                          v-for="col in writeRowsColumnKeys(w.rows)"
                          :key="col"
                          class="mono"
                        >
                          {{ col }}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="(row, ri) in w.rows" :key="'r' + ri">
                        <td v-for="col in writeRowsColumnKeys(w.rows)" :key="col">
                          {{ formatPreviewValue(row[col]) }}
                        </td>
                      </tr>
                    </tbody>
                  </table>
                  <p v-else class="mono writes-hint">（无字段明细）</p>
                </div>
                <p v-if="msg.preview.reason" class="mono writes-reason">
                  <strong>原因：</strong>{{ msg.preview.reason }}
                </p>
              </template>
              <template v-else-if="isContractPreview(msg.preview)">
                <div class="msg-confirm-subtitle">合同主信息</div>
                <table class="msg-plain-table msg-confirm-preview-table">
                  <thead>
                    <tr>
                      <th>字段</th>
                      <th>值</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="(ent, ri) in contractMainEntries(msg.preview)" :key="'m' + ri">
                      <td class="mono">{{ ent[0] }}</td>
                      <td>{{ formatPreviewValue(ent[1]) }}</td>
                    </tr>
                  </tbody>
                </table>
                <div class="msg-confirm-subtitle msg-confirm-subtitle-items">产品明细</div>
                <table class="msg-plain-table msg-confirm-preview-table">
                  <thead>
                    <tr>
                      <th v-for="col in contractItemColumnKeys(msg.preview)" :key="col" class="mono">
                        {{ col }}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="(row, ri) in (msg.preview.items || [])" :key="'i' + ri">
                      <td v-for="col in contractItemColumnKeys(msg.preview)" :key="col">
                        {{ formatPreviewValue(row[col]) }}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </template>
              <template v-else-if="isContractUpdatePreview(msg.preview)">
                <div class="msg-confirm-subtitle">
                  合同 {{ msg.preview.contract_no || '（编号）' }}
                </div>
                <div class="msg-confirm-explanation contract-update-reason">
                  <strong>变更原因：</strong>{{ msg.preview.reason }}
                </div>
                <div class="msg-confirm-subtitle">总额对比</div>
                <table class="msg-plain-table msg-confirm-preview-table">
                  <thead>
                    <tr>
                      <th></th>
                      <th>变更前</th>
                      <th>变更后</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td>total_amount</td>
                      <td>{{ formatPreviewValue(contractUpdateBeforeAfter(msg.preview).before) }}</td>
                      <td>{{ formatPreviewValue(contractUpdateBeforeAfter(msg.preview).after) }}</td>
                    </tr>
                  </tbody>
                </table>
                <div class="msg-confirm-subtitle msg-confirm-subtitle-items">明细行数</div>
                <p class="mono contract-update-hint">
                  变更前 {{ (msg.preview.snapshot_before.items || []).length }} 行 → 变更后
                  {{ (msg.preview.snapshot_after.items || []).length }} 行（含预览中的新增行标记）
                </p>
              </template>
              <table v-else class="msg-plain-table msg-confirm-preview-table">
                <thead>
                  <tr>
                    <th>字段</th>
                    <th>值</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(ent, ri) in previewRowEntries(msg.preview)" :key="ri">
                    <td class="mono">{{ ent[0] }}</td>
                    <td>{{ formatPreviewValue(ent[1]) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div class="msg-confirm-actions">
              <button
                type="button"
                class="confirm-write-btn"
                :disabled="loading"
                @click="onConfirmEntityWrite(i)"
              >
                ✅ 确认写入
              </button>
              <button
                type="button"
                class="cancel-write-btn"
                :disabled="loading"
                @click="onCancelEntityWrite(i)"
              >
                ❌ 取消
              </button>
            </div>
          </div>
        </template>

        <template v-else-if="msg.role === 'system'">
          <div class="msg-meta mono">系统</div>
          <div class="msg-body msg-system-line">{{ msg.content }}</div>
        </template>

        <template v-else>
        <div class="msg-meta mono">{{ msg.role === 'user' ? '你' : 'AI' }}</div>
        <div class="msg-body" v-html="renderMarkdown(msg.content)"></div>
        <template v-if="msg.role === 'assistant'">
          <div
            v-if="(msg.tableMain || []).length > 0"
            class="msg-table-block"
          >
            <div class="msg-table-title">查询结果</div>
            <div class="msg-table-scroll">
              <table class="msg-plain-table">
                <thead>
                  <tr>
                    <th v-for="col in buildColumnsFromRows(msg.tableMain)" :key="col.key">
                      {{ col.title }}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(row, ri) in msg.tableMain" :key="ri">
                    <td v-for="col in buildColumnsFromRows(msg.tableMain)" :key="col.key">
                      {{ row[col.key] ?? '' }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <div
            v-if="(msg.tableSub || []).length > 0"
            class="msg-table-block msg-table-sub"
          >
            <div class="msg-table-title">关联明细</div>
            <div class="msg-table-scroll">
              <table class="msg-plain-table">
                <thead>
                  <tr>
                    <th v-for="col in buildColumnsFromRows(msg.tableSub)" :key="col.key">
                      {{ col.title }}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(row, ri) in msg.tableSub" :key="ri">
                    <td v-for="col in buildColumnsFromRows(msg.tableSub)" :key="col.key">
                      {{ row[col.key] ?? '' }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </template>
        <div
          v-if="showAssistantFeedback(msg)"
          class="feedback-area"
        >
          <template v-if="feedbackOpenIndex === i">
            <div class="feedback-input-area">
              <textarea
                v-model="feedbackDraft"
                placeholder="哪里不对？简单说几句就行"
                rows="2"
                maxlength="500"
              />
              <div class="feedback-actions">
                <button type="button" class="feedback-submit" @click="submitFeedback(i)">
                  提交
                </button>
                <button type="button" class="feedback-cancel" @click="closeFeedback">取消</button>
              </div>
            </div>
          </template>
          <template v-else>
            <button
              v-if="!msg.feedbackSent"
              type="button"
              class="feedback-btn"
              title="回答有问题"
              @click="openFeedback(i)"
            >
              👎
            </button>
            <span v-else class="feedback-done">已反馈</span>
          </template>
        </div>
        <div
          v-if="msg.role === 'assistant' && msg.writePreview"
          class="msg-write-preview-card"
        >
          <div class="msg-confirm-title">请确认以下操作</div>
          <div class="msg-write-preview-section">
            <div class="msg-write-preview-label">操作确认</div>
            <div
              class="msg-write-preview-body"
              v-html="highlightedWritePreviewSummary(msg.writePreview)"
            ></div>
          </div>
          <div
            v-if="writePreviewExpertExplanation(msg.writePreview)"
            class="msg-write-preview-expert"
          >
            <div class="msg-write-preview-label msg-write-preview-label-sub">操作说明</div>
            <div class="msg-write-preview-expert-text">
              {{ writePreviewExpertExplanation(msg.writePreview) }}
            </div>
          </div>
          <p
            v-if="msg.writePreview.payload?.batch?.length > 1"
            class="msg-write-preview-meta"
          >
            共 {{ msg.writePreview.payload.batch.length }} 条 SQL，确认后同一事务顺序执行
          </p>
          <div class="msg-write-preview-actions">
            <button
              type="button"
              class="cancel-write-btn"
              :disabled="loading"
              @click="cancelWritePreview(i)"
            >
              取消
            </button>
            <button
              type="button"
              class="confirm-write-btn"
              :disabled="loading"
              @click="confirmWritePreview(i)"
            >
              确认执行
            </button>
          </div>
        </div>
      <div v-if="msg.role === 'assistant' && msg.requiresConfirm" class="confirm-wrap">
        <button class="confirm-btn" @click="confirmOperation" :disabled="loading">
          {{ msg.pendingToken ? '确认执行' : '【确认】' }}
        </button>
      </div>
        </template>
      </div>

      <!-- 等待首包（意图判断完成前）；流式输出阶段不重复显示，避免与正文气泡重叠 -->
      <div v-if="loading && awaitingStreamMeta" class="message assistant">
        <div class="msg-meta mono">AI</div>
        <div class="msg-body thinking">
          <span></span><span></span><span></span>
        </div>
      </div>
    </div>

    <!-- 输入框 -->
    <div class="ai-composer">
      <div class="ai-memory-hint">
        <span class="ai-memory-label" title="当前会话对话清洗为要点，写入 ai_memory(scope=session)"
          >会话要点</span
        >
        <span class="ai-memory-sep" aria-hidden="true">·</span>
        <span
          class="ai-memory-label"
          title="合并今日 session 要点与旧 longterm，写入 ai_memory(scope=longterm)，供后续对话注入"
          >长期记忆</span
        >
      </div>
      <div class="ai-input-wrap">
        <button class="plus-btn" @click="triggerFilePicker" :disabled="loading" title="上传文件">
          +
        </button>
        <textarea
          v-model="inputText"
          class="ai-input"
          placeholder="输入问题… 仅可通过右侧按钮发送"
          rows="3"
          :disabled="loading"
        ></textarea>
        <input
          ref="fileInputRef"
          type="file"
          multiple
          style="display: none"
          :accept="allowedAccept"
          @change="handleFilesSelected"
        />
        <button
          type="button"
          class="memory-btn"
          :disabled="loading || memoryLoading || longtermMemoryLoading || !chatSessionId"
          title="将当前会话对话清洗为要点，写入 ai_memory（scope=session），供后续注入"
          @click="generateSessionMemory"
        >
          <span class="memory-glyph" aria-hidden="true">🧠</span>
        </button>
        <button
          type="button"
          class="memory-btn memory-btn-longterm"
          :disabled="loading || memoryLoading || longtermMemoryLoading || !authStore.currentUser?.id"
          title="手动触发长期记忆压缩（等同日后定时任务），需已登录"
          @click="compressLongtermMemory"
        >
          <span class="memory-glyph" aria-hidden="true">📦</span>
        </button>
        <button class="send-btn" @click="handleSend" :disabled="loading || !inputText.trim()">
          <span>↑</span>
        </button>
      </div>
    </div>
  </aside>
</template>

<script setup>
import { ref, nextTick } from 'vue'

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/** 写入预览摘要：将后端下发的危险词标红加粗 */
function highlightedWritePreviewSummary(wp) {
  let text = escapeHtml(wp?.summary || '')
  const words = wp?.highlight_words || []
  words.forEach((w) => {
    const kw = String(w)
    if (!kw) return
    text = text.replaceAll(kw, `<span style="color: #e53e3e; font-weight: bold;">${escapeHtml(kw)}</span>`)
  })
  return text
}

/** LLM expert 业务说明（与引擎 summary 分列展示） */
function writePreviewExpertExplanation(wp) {
  const t = wp?.expert_explanation ?? wp?.payload?.expert_explanation
  return t != null && String(t).trim() !== '' ? String(t).trim() : ''
}
import { useAuthStore } from '../stores/auth'
import { API_BASE } from '@/config/api'

const authStore = useAuthStore()

/** 仅前端展示的欢迎语（不写入 ai_chat；发往 /ai/chat 时在 buildApiHistory 中排除） */
const localWelcomeText =
  '您好！我是您的数字助手。\n\n' +
  '我能帮您处理公司的各项业务——查数据、下订单、管库存、做对账，虽然我还在学习中，但能力会越来越强。\n\n' +
  '我也能为您统计和展现公司经营数据，用数字、图表让全局一目了然。\n\n' +
  '您交代过的事情和偏好我会记在心里，不过时间久了可能会慢慢淡忘，重要的事您可以提醒我。\n\n' +
  '总之，尽管告诉我，也许会让您大吃一惊。'

function buildColumnsFromRows(rows) {
  if (!rows?.length) return []
  return Object.keys(rows[0]).map((key) => ({
    title: key,
    key,
    ellipsis: true,
  }))
}

function writeSegmentTitle(w) {
  if (!w || typeof w !== 'object') return '操作'
  const op = (w.operation || '').trim()
  const tbl = (w.table || '').trim()
  const ops = op === 'insert' ? '新建' : op === 'update' ? '更新' : op === 'soft_delete' ? '软删' : op || '操作'
  return tbl ? `${ops} ${tbl}` : ops
}

function writeRowsColumnKeys(rows) {
  if (!Array.isArray(rows) || !rows.length) return []
  const keys = new Set()
  for (const row of rows) {
    if (row && typeof row === 'object') {
      for (const k of Object.keys(row)) keys.add(k)
    }
  }
  return Array.from(keys)
}

/** collecting 待确认：insert 用 preview.row，update 用 preview.fields */
function previewRowEntries(preview) {
  const row = preview?.row || preview?.fields
  if (!row || typeof row !== 'object') return []
  return Object.entries(row)
}

function isContractPreview(preview) {
  return (
    preview &&
    typeof preview.contract === 'object' &&
    preview.contract !== null &&
    Array.isArray(preview.items) &&
    !preview.snapshot_before
  )
}

function isWritesPreview(preview) {
  return (
    preview &&
    Array.isArray(preview.writes) &&
    preview.writes.length > 0 &&
    !isContractPreview(preview)
  )
}

function isContractUpdatePreview(preview) {
  return (
    preview &&
    typeof preview.reason === 'string' &&
    preview.reason.trim() !== '' &&
    preview.snapshot_before &&
    preview.snapshot_after
  )
}

function contractUpdateBeforeAfter(preview) {
  const b = preview?.snapshot_before?.contract?.total_amount
  const a = preview?.snapshot_after?.contract?.total_amount
  return { before: b, after: a }
}

function contractMainEntries(preview) {
  if (!isContractPreview(preview)) return []
  return Object.entries(preview.contract)
}

function contractItemColumnKeys(preview) {
  const items = preview?.items
  if (!Array.isArray(items) || !items.length) return []
  const keys = new Set()
  for (const row of items) {
    if (row && typeof row === 'object') {
      for (const k of Object.keys(row)) keys.add(k)
    }
  }
  return Array.from(keys)
}

function formatPreviewValue(v) {
  if (v === null || v === undefined) return ''
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

const feedbackOpenIndex = ref(null)
const feedbackDraft = ref('')

function showAssistantFeedback(msg) {
  return (
    msg &&
    msg.role === 'assistant' &&
    !msg.isLocalWelcome &&
    !msg.writePreview &&
    !msg.confirmRequired
  )
}

function openFeedback(i) {
  feedbackOpenIndex.value = i
  feedbackDraft.value = ''
}

function closeFeedback() {
  feedbackOpenIndex.value = null
}

async function submitFeedback(i) {
  const uid = authStore.currentUser?.id
  if (uid == null) {
    window.alert('请先登录后再反馈')
    return
  }
  const sid = chatSessionId.value
  try {
    const res = await fetch(`${API_BASE}/ai/feedback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sid,
        user_id: uid,
        chat_id: null,
        feedback_type: 'thumbs_down',
        content: feedbackDraft.value || '',
      }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      const d = data?.detail || data?.message || '提交失败'
      throw new Error(typeof d === 'string' ? d : JSON.stringify(d))
    }
    const m = messages.value[i]
    if (m) m.feedbackSent = true
    feedbackOpenIndex.value = null
    feedbackDraft.value = ''
  } catch (e) {
    window.alert(e?.message || '提交失败')
  }
}

const chatSessionId = ref(null)
const lastFrameId = ref(null)
const lastErrorMessage = ref(null)

/** 将仅发生在前端的收尾文案同步到服务端 ai_chat（确认结果由 /ai/confirm 写入） */
async function logChatMessageToServer(content, role = 'assistant') {
  const sid = chatSessionId.value
  if (sid == null || !content) return
  try {
    await fetch(`${API_BASE}/ai/chat/log-message`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sid, content, role }),
    })
  } catch {
    /* 不影响主流程 */
  }
}

async function cancelWritePreview(idx) {
  const m = messages.value[idx]
  if (!m?.writePreview) return
  messages.value[idx] = { ...m, writePreview: null }
  messages.value.push({
    role: 'assistant',
    content: '您已经取消任务。',
  })
  await logChatMessageToServer('您已经取消任务。')
  await scrollToBottom()
}

async function confirmWritePreview(idx) {
  const m = messages.value[idx]
  if (!m?.writePreview?.payload || loading.value) return
  loading.value = true
  try {
    const res = await fetch(`${API_BASE}/ai/confirm`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        payload: m.writePreview.payload,
        session_id: chatSessionId.value,
        user_id: authStore.currentUser?.id,
      }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      const detail = data?.detail || data?.error || '请求失败'
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
    }
    messages.value[idx] = { ...m, writePreview: null }
    let line
    if (data.success) {
      const vm = typeof data.verify_message === 'string' ? data.verify_message.trim() : ''
      line = vm ? `✅ ${vm}` : '✅ 已经按照您的要求完成任务。'
    } else {
      line = data.task_failed
        ? `❌ ${data.error || '任务失败'}`
        : `❌ 执行失败：${data.error || '未知错误'}`
    }
    messages.value.push({ role: 'assistant', content: line })
    await scrollToBottom()
  } catch (e) {
    const errLine = `⚠ 确认执行失败：${e?.message || e}`
    messages.value.push({
      role: 'assistant',
      content: errLine,
    })
    await logChatMessageToServer(errLine)
    await scrollToBottom()
  } finally {
    loading.value = false
  }
}

async function postAiConfirm(frameId, confirmed) {
  const res = await fetch(`${API_BASE}/ai/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      frame_id: frameId,
      confirmed,
      user_id: authStore.currentUser?.id,
    }),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    const detail = data?.detail || data?.message || '请求失败'
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return res.json()
}

async function onConfirmEntityWrite(idx) {
  const msg = messages.value[idx]
  if (!msg || loading.value || !msg.frameId) return
  loading.value = true
  try {
    const data = await postAiConfirm(msg.frameId, true)
    const cr = data?.confirm_result
    const isErr = cr === 'error'
    const detail =
      (typeof data?.error_detail === 'string' && data.error_detail.trim()
        ? data.error_detail.trim()
        : '') ||
      (typeof data?.content === 'string' ? data.content.trim() : '') ||
      '执行失败'
    messages.value.splice(idx, 1)
    const done =
      !isErr &&
      data &&
      typeof data.content === 'string' &&
      data.content.trim()
        ? data.content.trim()
        : isErr
          ? detail
          : '✅ 已写入'
    const outLine = isErr ? `❌ ${detail}` : done
    messages.value.push({
      role: 'assistant',
      content: outLine,
      confirmExecError: !!isErr,
    })
    await logChatMessageToServer(outLine)
    await scrollToBottom()
  } catch (e) {
    const errLine = `⚠ ${e?.message || '确认失败'}`
    messages.value.push({
      role: 'assistant',
      content: errLine,
      confirmExecError: true,
    })
    await logChatMessageToServer(errLine)
    await scrollToBottom()
  } finally {
    loading.value = false
  }
}

async function onCancelEntityWrite(idx) {
  const msg = messages.value[idx]
  if (!msg || loading.value || !msg.frameId) return
  loading.value = true
  try {
    await postAiConfirm(msg.frameId, false)
    messages.value.splice(idx, 1)
    messages.value.push({ role: 'assistant', content: '已取消' })
    await logChatMessageToServer('已取消')
    await scrollToBottom()
  } catch (e) {
    const errLine = `⚠ ${e?.message || '取消失败'}`
    messages.value.push({
      role: 'assistant',
      content: errLine,
    })
    await logChatMessageToServer(errLine)
    await scrollToBottom()
  } finally {
    loading.value = false
  }
}

/** 调试：确认后端下发的行是否为对象数组、字段名与列一致 */
function logTableMain(tag, tableMain, tableSub) {
  try {
    console.log(`[AiPanel] ${tag} tableMain:`, JSON.stringify(tableMain))
    if (tableSub?.length) console.log(`[AiPanel] ${tag} tableSub:`, JSON.stringify(tableSub))
  } catch (e) {
    console.log(`[AiPanel] ${tag} tableMain (raw):`, tableMain)
  }
}

const messages = ref([])
const inputText = ref('')
const loading = ref(false)
/** 流式对话：收到首条 SSE meta 前为 true，用于显示「思考中」而不与正文流重复 */
const awaitingStreamMeta = ref(true)
const chatBodyRef = ref(null)
const fileInputRef = ref(null)
const pendingFiles = ref([]) // 后端需要的 "<filename>|<base64>" 字符串数组
/** @type {import('vue').Ref<AbortController | null>} */
const chatAbortController = ref(null)
/** 生成会话记忆（调用 /ai/memory/clean-session）进行中 */
const memoryLoading = ref(false)
/** 长期记忆压缩（调用 /ai/memory/compress-longterm）进行中 */
const longtermMemoryLoading = ref(false)

function stopGeneration() {
  chatAbortController.value?.abort()
}

const allowedAccept =
  'image/jpeg,image/png,image/webp,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel,application/pdf'

function getFileExt(filename) {
  const lower = (filename || '').toLowerCase()
  const i = lower.lastIndexOf('.')
  return i >= 0 ? lower.slice(i + 1) : ''
}

function fileToPayload(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      try {
        const dataUrl = reader.result || ''
        // dataUrl: "data:<mime>;base64,<...>"
        const b64 = String(dataUrl).split(',')[1] || ''
        resolve(`${file.name}|${b64}`)
      } catch (e) {
        reject(e)
      }
    }
    reader.onerror = (e) => reject(e)
    reader.readAsDataURL(file)
  })
}

function renderMarkdown(text) {
  // 简单 Markdown 渲染：代码块、加粗、换行
  return text
    .replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\n/g, '<br>')
}

function clearChat() {
  stopGeneration()
  messages.value = []
  pendingFiles.value = []
  chatSessionId.value = null
}

/** 从 SSE 首包写入会话 id，后续请求带上才能落库 ai_chat */
function applySessionFromSseStart(payload) {
  if (payload.session_id != null && payload.session_id !== undefined) {
    chatSessionId.value = payload.session_id
  }
}

/**
 * 构造发给 /ai/chat 的 messages：去掉无正文的空助手壳（除非仍待确认），
 * 并去掉连续完全相同的相邻条，避免历史污染与重复 user。
 */
function buildApiHistory() {
  const step1 = messages.value.filter((m) => {
    if (!m || !m.role) return false
    if (m.isLocalWelcome) return false
    if (m.role === 'system') return false
    if (m.role === 'assistant') {
      const c = (m.content || '').trim()
      const hasTable = (m.tableMain && m.tableMain.length > 0) || (m.tableSub && m.tableSub.length > 0)
      if (!c && !m.requiresConfirm && !hasTable && !m.confirmRequired) return false
    }
    return true
  })
  const out = []
  for (const m of step1) {
    const row = {
      role: m.role,
      content: typeof m.content === 'string' ? m.content : '',
    }
    const prev = out[out.length - 1]
    if (
      prev &&
      prev.role === row.role &&
      prev.content === row.content
    ) {
      continue
    }
    out.push(row)
  }
  return out.slice(-12)
}

/** 流式结束后删掉仍无正文且无需确认的助手占位，减轻列表垃圾 */
function pruneOrphanEmptyAssistants() {
  messages.value = messages.value.filter((m) => {
    if (!m || m.role !== 'assistant') return true
    const c = (m.content || '').trim()
    if (c) return true
    if (m.requiresConfirm) return true
    if (m.confirmRequired) return true
    if ((m.tableMain && m.tableMain.length > 0) || (m.tableSub && m.tableSub.length > 0))
      return true
    return false
  })
}

async function scrollToBottom() {
  await nextTick()
  if (chatBodyRef.value) {
    chatBodyRef.value.scrollTop = chatBodyRef.value.scrollHeight
  }
}

/** 调用后端清洗当前会话 → 写入 business.ai_memory（scope=session） */
async function generateSessionMemory() {
  if (!chatSessionId.value) return
  memoryLoading.value = true
  try {
    const res = await fetch(`${API_BASE}/ai/memory/clean-session`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: chatSessionId.value }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      const detail = data?.detail || data?.message || data?.error || '请求失败'
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
    }
    const full = data.content || ''
    const preview = full.length > 800 ? `${full.slice(0, 800)}…` : full
    messages.value.push({
      role: 'assistant',
      content:
        `✅ 已生成会话记忆并写入库（scope=session，session_id=${data.session_id ?? chatSessionId.value}）。\n\n` +
        (preview ? `要点预览：\n${preview}` : '（内容为空）'),
    })
    await scrollToBottom()
  } catch (e) {
    messages.value.push({
      role: 'assistant',
      content: `⚠ 生成记忆失败：${e?.message || e}`,
    })
    await scrollToBottom()
  } finally {
    memoryLoading.value = false
  }
}

/** 手动触发长期记忆压缩 → 写入 ai_memory(scope=longterm)，用于测试（日后可由凌晨定时任务调用同一接口） */
async function compressLongtermMemory() {
  const uid = authStore.currentUser?.id
  if (uid == null) return
  longtermMemoryLoading.value = true
  try {
    const res = await fetch(`${API_BASE}/ai/memory/compress-longterm`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: uid }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      const detail = data?.detail || data?.message || data?.error || '请求失败'
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
    }
    const full = data.content || ''
    const preview = full.length > 800 ? `${full.slice(0, 800)}…` : full
    messages.value.push({
      role: 'assistant',
      content:
        `✅ 已压缩并写入长期记忆（scope=longterm，user_id=${data.user_id ?? uid}）。\n\n` +
        (preview ? `内容预览：\n${preview}` : '（内容为空）'),
    })
    await scrollToBottom()
  } catch (e) {
    messages.value.push({
      role: 'assistant',
      content: `⚠ 长期记忆压缩失败：${e?.message || e}`,
    })
    await scrollToBottom()
  } finally {
    longtermMemoryLoading.value = false
  }
}

function triggerFilePicker() {
  if (fileInputRef.value) fileInputRef.value.click()
}

async function handleFilesSelected(e) {
  const picked = Array.from(e.target.files || [])
  if (!picked.length) return

  const allowedExt = new Set(['jpg', 'jpeg', 'png', 'webp', 'xlsx', 'xls', 'pdf'])
  const payloads = []

  for (const f of picked) {
    const ext = getFileExt(f.name)
    if (!allowedExt.has(ext)) {
      window.alert(`不支持该文件类型：${f.name}`)
      continue
    }
    payloads.push(await fileToPayload(f))
  }

  pendingFiles.value = payloads
  // 让同一文件可重复选择
  e.target.value = ''
}

async function handleSend() {
  const text = inputText.value.trim()
  if (!text || loading.value) return
  // 若存在待确认的「操作」复述，用户输入新内容时先移除旧确认按钮（避免多个按钮）
  messages.value = messages.value.map((m) =>
    m && (m.requiresConfirm || m.confirmRequired)
      ? {
          ...m,
          requiresConfirm: false,
          pendingToken: null,
          confirmRequired: false,
          frameId: null,
          preview: null,
          explanation: null,
        }
      : m
  )
  await sendMessage(text)
}

function parseSseBlocks(buffer) {
  const events = []
  const parts = buffer.split('\n\n')
  const rest = parts.pop() ?? ''
  for (const block of parts) {
    const line = block.trim()
    if (!line.startsWith('data:')) continue
    const raw = line.slice(5).trim()
    try {
      events.push(JSON.parse(raw))
    } catch {
      /* 忽略损坏行 */
    }
  }
  return { events, rest }
}

/** 消费 SSE 正文，按事件回调（与首轮对话相同协议） */
async function readSseStreamBody(res, handleStreamPayload) {
  const reader = res.body?.getReader()
  if (!reader) {
    throw new Error('无法读取响应流')
  }
  const decoder = new TextDecoder()
  let buf = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const { events, rest } = parseSseBlocks(buf)
    buf = rest
    for (const payload of events) {
      await handleStreamPayload(payload)
    }
  }
  buf += decoder.decode()
  if (buf.trim()) {
    const tail = buf.endsWith('\n\n') ? buf : `${buf}\n\n`
    const { events } = parseSseBlocks(tail)
    for (const payload of events) {
      await handleStreamPayload(payload)
    }
  }
}

async function sendMessage(text) {
  inputText.value = ''
  if (messages.value.length === 0) {
    messages.value.push({
      role: 'assistant',
      content: localWelcomeText,
      isLocalWelcome: true,
    })
  }
  messages.value.push({ role: 'user', content: text })
  await scrollToBottom()

  loading.value = true
  awaitingStreamMeta.value = true
  const ac = new AbortController()
  chatAbortController.value = ac

  const state = { assistantIdx: -1 }

  async function handleStreamPayload(payload) {
    if (payload.type === 'start') {
      applySessionFromSseStart(payload)
      return
    }
    if (payload.type === 'meta' && payload.patch) {
      if (state.assistantIdx >= 0) {
        const cur = messages.value[state.assistantIdx]
        const dp = payload.data
        const next = {
          ...cur,
          requiresConfirm: !!payload.requiresConfirm,
          pendingToken:
            payload.pendingToken !== undefined
              ? payload.pendingToken
              : cur.pendingToken,
        }
        if (dp && typeof dp === 'object') {
          if (Array.isArray(dp.main)) next.tableMain = dp.main
          if (Array.isArray(dp.sub)) next.tableSub = dp.sub
        }
        messages.value[state.assistantIdx] = next
      }
      await scrollToBottom()
      return
    }
    if (payload.type === 'meta') {
      awaitingStreamMeta.value = false
      const summaries = payload.fileSummaries
      if (Array.isArray(summaries)) {
        for (const s of summaries) {
          const t = s != null ? String(s).trim() : ''
          if (t) messages.value.push({ role: 'assistant', content: t })
        }
      }
      const dp = payload.data
      const tableMain = Array.isArray(dp?.main) ? dp.main : []
      const tableSub = Array.isArray(dp?.sub) ? dp.sub : []
      state.assistantIdx = messages.value.length
      messages.value.push({
        role: 'assistant',
        content: '',
        requiresConfirm: !!payload.requiresConfirm,
        pendingToken: payload.pendingToken ?? null,
        tableMain,
        tableSub,
      })
      await scrollToBottom()
    } else if (payload.type === 'delta' && state.assistantIdx >= 0) {
      const cur = messages.value[state.assistantIdx]
      messages.value[state.assistantIdx] = {
        ...cur,
        content: (cur.content || '') + (payload.text || ''),
      }
      await scrollToBottom()
    } else if (payload.type === 'error') {
      const msg = payload.detail || '错误'
      lastErrorMessage.value = String(msg)
      if (state.assistantIdx >= 0) {
        const cur = messages.value[state.assistantIdx]
        messages.value[state.assistantIdx] = {
          ...cur,
          content: (cur.content || '') + `\n\n⚠ ${msg}`,
        }
      } else {
        messages.value.push({ role: 'assistant', content: `⚠ ${msg}` })
      }
      await scrollToBottom()
    } else if (payload.type === 'write_preview') {
      if (state.assistantIdx >= 0) {
        const cur = messages.value[state.assistantIdx]
        messages.value[state.assistantIdx] = {
          ...cur,
          writePreview: {
            summary: payload.summary || '',
            expert_explanation: payload.expert_explanation,
            highlight_words: payload.highlight_words || [],
            payload: payload.payload || null,
          },
        }
      }
      await scrollToBottom()
    } else if (payload.type === 'write_preview_error') {
      if (state.assistantIdx >= 0) {
        const cur = messages.value[state.assistantIdx]
        messages.value[state.assistantIdx] = {
          ...cur,
          content:
            (cur.content || '') +
            `\n\n⚠ 写入预览校验失败：${payload.error || '未知错误'}`,
        }
      }
      await scrollToBottom()
    } else if (payload.type === 'done') {
      if (state.assistantIdx >= 0 && payload.data && typeof payload.data === 'object') {
        const cur = messages.value[state.assistantIdx]
        const dp = payload.data
        const tm = Array.isArray(dp.main) ? dp.main : cur.tableMain || []
        const ts = Array.isArray(dp.sub) ? dp.sub : cur.tableSub || []
        messages.value[state.assistantIdx] = {
          ...cur,
          tableMain: tm,
          tableSub: ts,
        }
        if (tm?.length || ts?.length) logTableMain('/ai/chat SSE done', tm, ts)
      }
      loading.value = false
      awaitingStreamMeta.value = true
    }
  }

  try {
    const history = buildApiHistory()

    const res = await fetch(`${API_BASE}/ai/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        messages: history,
        files: pendingFiles.value,
        confirm: false,
        stream: true,
        user_id: authStore.currentUser?.id,
        session_id: chatSessionId.value,
      }),
      signal: ac.signal,
    })

    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      const detail = data?.detail || data?.message || '请求失败'
      throw new Error(detail)
    }

    const ct = (res.headers.get('content-type') || '').toLowerCase()
    if (ct.includes('application/json')) {
      const data = await res.json()
      if (data.session_id != null && data.session_id !== undefined) {
        chatSessionId.value = data.session_id
      }
      if (data.frame_id != null && data.frame_id !== undefined) {
        lastFrameId.value = data.frame_id
      }
      const summaries = data.fileSummaries
      if (Array.isArray(summaries)) {
        for (const s of summaries) {
          const t = s != null ? String(s).trim() : ''
          if (t) messages.value.push({ role: 'assistant', content: t })
        }
      }
      const reply = data.reply ?? data.content ?? ''
      const tableMain = Array.isArray(data.data?.main) ? data.data.main : []
      const tableSub = Array.isArray(data.data?.sub) ? data.data.sub : []
      if (data.stage === 'preview' && data.payload) {
        messages.value.push({
          role: 'assistant',
          content: reply || '',
          tableMain: [],
          tableSub: [],
          requiresConfirm: false,
          pendingToken: null,
          writePreview: {
            summary: data.summary || '',
            expert_explanation: data.expert_explanation ?? data.payload?.expert_explanation,
            highlight_words: data.highlight_words || [],
            payload: data.payload,
          },
        })
      } else if (data.confirm_required) {
        messages.value.push({
          role: 'assistant',
          content: reply || '请确认以下新建内容。',
          confirmRequired: true,
          frameId: data.frame_id ?? null,
          preview: data.preview ?? null,
          explanation: data.explanation ?? '',
          tableMain: [],
          tableSub: [],
          requiresConfirm: false,
          pendingToken: null,
        })
      } else {
        messages.value.push({
          role: 'assistant',
          content: reply,
          tableMain,
          tableSub,
          requiresConfirm: !!data.requiresConfirm,
          pendingToken: data.pendingToken ?? null,
        })
      }
      if (tableMain?.length || tableSub?.length) {
        logTableMain('/ai/chat JSON', tableMain, tableSub)
      }
    } else {
      await readSseStreamBody(res, handleStreamPayload)
    }
  } catch (e) {
    const aborted = e?.name === 'AbortError' || ac.signal.aborted
    if (aborted) {
      messages.value.push({
        role: 'system',
        content: '用户已终止自动执行。',
      })
    } else {
      const em = e?.message || '请求失败'
      lastErrorMessage.value = String(em)
      messages.value.push({
        role: 'assistant',
        content: `⚠ ${em}`,
      })
    }
  } finally {
    pruneOrphanEmptyAssistants()
    chatAbortController.value = null
    loading.value = false
    awaitingStreamMeta.value = true
    pendingFiles.value = []
    await scrollToBottom()
  }
}

async function confirmOperation() {
  if (loading.value) return

  loading.value = true
  awaitingStreamMeta.value = true
  const ac = new AbortController()
  chatAbortController.value = ac

  let lastIdx = -1
  let confirmSnapshot = null

  const state = { assistantIdx: -1 }

  async function handleStreamPayload(payload) {
    if (payload.type === 'start') {
      applySessionFromSseStart(payload)
      return
    }
    if (payload.type === 'meta' && payload.patch) {
      if (state.assistantIdx >= 0) {
        const cur = messages.value[state.assistantIdx]
        const dp = payload.data
        const next = {
          ...cur,
          requiresConfirm: !!payload.requiresConfirm,
          pendingToken:
            payload.pendingToken !== undefined
              ? payload.pendingToken
              : cur.pendingToken,
        }
        if (dp && typeof dp === 'object') {
          if (Array.isArray(dp.main)) next.tableMain = dp.main
          if (Array.isArray(dp.sub)) next.tableSub = dp.sub
        }
        messages.value[state.assistantIdx] = next
      }
      await scrollToBottom()
      return
    }
    if (payload.type === 'meta') {
      awaitingStreamMeta.value = false
      const summaries = payload.fileSummaries
      if (Array.isArray(summaries)) {
        for (const s of summaries) {
          const t = s != null ? String(s).trim() : ''
          if (t) messages.value.push({ role: 'assistant', content: t })
        }
      }
      const dp = payload.data
      const tableMain = Array.isArray(dp?.main) ? dp.main : []
      const tableSub = Array.isArray(dp?.sub) ? dp.sub : []
      state.assistantIdx = messages.value.length
      messages.value.push({
        role: 'assistant',
        content: '',
        requiresConfirm: !!payload.requiresConfirm,
        pendingToken: payload.pendingToken ?? null,
        tableMain,
        tableSub,
      })
      await scrollToBottom()
    } else if (payload.type === 'delta' && state.assistantIdx >= 0) {
      const cur = messages.value[state.assistantIdx]
      messages.value[state.assistantIdx] = {
        ...cur,
        content: (cur.content || '') + (payload.text || ''),
      }
      await scrollToBottom()
    } else if (payload.type === 'write_preview') {
      if (state.assistantIdx >= 0) {
        const cur = messages.value[state.assistantIdx]
        messages.value[state.assistantIdx] = {
          ...cur,
          writePreview: {
            summary: payload.summary || '',
            expert_explanation: payload.expert_explanation,
            highlight_words: payload.highlight_words || [],
            payload: payload.payload || null,
          },
        }
      }
      await scrollToBottom()
    } else if (payload.type === 'write_preview_error') {
      if (state.assistantIdx >= 0) {
        const cur = messages.value[state.assistantIdx]
        messages.value[state.assistantIdx] = {
          ...cur,
          content:
            (cur.content || '') +
            `\n\n⚠ 写入预览校验失败：${payload.error || '未知错误'}`,
        }
      }
      await scrollToBottom()
    } else if (payload.type === 'error') {
      const msg = payload.detail || '错误'
      lastErrorMessage.value = String(msg)
      if (state.assistantIdx >= 0) {
        const cur = messages.value[state.assistantIdx]
        messages.value[state.assistantIdx] = {
          ...cur,
          content: (cur.content || '') + `\n\n⚠ ${msg}`,
        }
      } else {
        messages.value.push({ role: 'assistant', content: `⚠ ${msg}` })
      }
      await scrollToBottom()
    } else if (payload.type === 'done') {
      if (state.assistantIdx >= 0 && payload.data && typeof payload.data === 'object') {
        const cur = messages.value[state.assistantIdx]
        const dp = payload.data
        const tm = Array.isArray(dp.main) ? dp.main : cur.tableMain || []
        const ts = Array.isArray(dp.sub) ? dp.sub : cur.tableSub || []
        messages.value[state.assistantIdx] = {
          ...cur,
          tableMain: tm,
          tableSub: ts,
        }
        if (tm?.length || ts?.length) logTableMain('/ai/chat confirm SSE done', tm, ts)
      }
      loading.value = false
      awaitingStreamMeta.value = true
    }
  }

  try {
    // 找到最近一条带确认按钮的复述消息，并移除按钮
    for (let i = messages.value.length - 1; i >= 0; i--) {
      if (messages.value[i]?.role === 'assistant' && messages.value[i]?.requiresConfirm) {
        lastIdx = i
        break
      }
    }
    const pendingTok =
      lastIdx >= 0 ? messages.value[lastIdx]?.pendingToken : null
    if (lastIdx >= 0) {
      confirmSnapshot = { ...messages.value[lastIdx] }
      messages.value[lastIdx] = {
        ...messages.value[lastIdx],
        requiresConfirm: false,
        pendingToken: null,
      }
    }

    const history = buildApiHistory()

    const confirmBody = {
      messages: history,
      files: [],
      confirm: true,
      stream: true,
      user_id: authStore.currentUser?.id,
      session_id: chatSessionId.value,
      ...(pendingTok ? { pendingToken: pendingTok } : {}),
    }

    const res = await fetch(`${API_BASE}/ai/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(confirmBody),
      signal: ac.signal,
    })

    const ct = (res.headers.get('content-type') || '').toLowerCase()
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      const detail = data?.detail || data?.message || '请求失败'
      throw new Error(detail)
    }

    if (ct.includes('text/event-stream')) {
      await readSseStreamBody(res, handleStreamPayload)
    } else {
      const data = await res.json().catch(() => ({}))
      const reply = data.reply ?? data.content ?? ''
      const tableMain = Array.isArray(data.data?.main) ? data.data.main : []
      const tableSub = Array.isArray(data.data?.sub) ? data.data.sub : []
      messages.value.push({
        role: 'assistant',
        content: reply,
        tableMain,
        tableSub,
        requiresConfirm: !!data.requiresConfirm,
        pendingToken: data.pendingToken ?? null,
      })
      if (tableMain?.length || tableSub?.length) {
        logTableMain('/ai/chat confirm JSON', tableMain, tableSub)
      }
    }
  } catch (e) {
    const aborted = e?.name === 'AbortError' || ac.signal.aborted
    if (aborted && lastIdx >= 0 && confirmSnapshot) {
      messages.value[lastIdx] = confirmSnapshot
    }
    if (aborted) {
      messages.value.push({
        role: 'system',
        content: '用户已终止自动执行。',
      })
    } else if (!aborted) {
      messages.value.push({
        role: 'assistant',
        content: `⚠ ${e?.message || '请求失败'}`,
      })
    }
  } finally {
    pruneOrphanEmptyAssistants()
    chatAbortController.value = null
    loading.value = false
    pendingFiles.value = []
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
.ai-header-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}
.stop-btn {
  background: var(--bg-active);
  border: 1px solid var(--border-hi);
  color: var(--text-1);
  cursor: pointer;
  font-size: 11px;
  line-height: 1;
  padding: 6px 10px;
  border-radius: var(--radius);
  font-family: var(--font-mono);
  transition: all var(--transition);
}
.stop-btn:hover {
  border-color: var(--accent);
  color: var(--accent);
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
  align-items: stretch;
  justify-content: flex-start;
  gap: 12px;
  text-align: left;
  padding: 8px 0 24px;
}
.empty-welcome {
  width: 100%;
}

.feedback-area {
  margin-top: 4px;
  text-align: right;
  width: 100%;
}
.feedback-btn {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 14px;
  opacity: 0.3;
  padding: 2px 6px;
  border-radius: 4px;
  transition: opacity 0.2s;
}
.feedback-btn:hover {
  opacity: 0.8;
  background: var(--bg-hover);
}
.feedback-done {
  font-size: 12px;
  color: var(--text-3);
}
.feedback-input-area {
  margin-top: 8px;
  padding: 8px;
  background: var(--bg-hover);
  border-radius: 6px;
  text-align: left;
}
.feedback-input-area textarea {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 6px;
  font-size: 13px;
  resize: none;
  font-family: var(--font-body);
  background: var(--bg-panel);
  color: var(--text-1);
}
.feedback-actions {
  margin-top: 6px;
  text-align: right;
}
.feedback-actions button {
  margin-left: 8px;
  padding: 4px 12px;
  font-size: 12px;
  border-radius: 4px;
  cursor: pointer;
}
.feedback-submit {
  background: var(--accent-hi, #1976d2);
  color: #fff;
  border: none;
}
.feedback-cancel {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  color: var(--text-2);
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
.message.assistant.msg-local-welcome .msg-body {
  background: var(--bg-hover);
  border: 1px solid var(--border);
  padding: 8px 12px;
  border-radius: var(--radius);
  text-align: left;
}
.message.system .msg-system-line {
  font-size: 12px;
  color: var(--text-3);
  font-style: italic;
  padding: 6px 0;
}

.msg-table-block {
  width: 100%;
  min-width: 0;
  margin-top: 10px;
}
.msg-table-block.msg-table-sub {
  margin-top: 14px;
}
.msg-table-title {
  font-size: 11px;
  color: var(--text-3);
  letter-spacing: 0.04em;
  margin-bottom: 6px;
}
.msg-table-scroll {
  width: 100%;
  min-width: 0;
  overflow-x: auto;
  max-height: 260px;
  overflow-y: auto;
}
.msg-plain-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  color: var(--text-2);
}
.msg-plain-table th,
.msg-plain-table td {
  border: 1px solid var(--border);
  padding: 5px 8px;
  white-space: nowrap;
  text-align: left;
}
.msg-plain-table th {
  background: var(--bg-active);
  color: var(--text-3);
  font-weight: 500;
  position: sticky;
  top: 0;
}
.msg-plain-table tr:hover td {
  background: var(--bg-hover);
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
.ai-composer {
  border-top: 1px solid var(--border);
  padding: 8px 16px 12px;
}
.ai-memory-hint {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 4px 6px;
  margin-bottom: 6px;
  min-height: 18px;
}
.ai-memory-label {
  font-size: 11px;
  color: var(--text-3);
  letter-spacing: 0.02em;
}
.ai-memory-sep {
  font-size: 11px;
  color: var(--text-3);
  opacity: 0.5;
  user-select: none;
}
.ai-input-wrap {
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

.plus-btn {
  width: 32px;
  height: 32px;
  background: var(--bg-active);
  border: 1px solid var(--border);
  color: var(--text-2);
  border-radius: var(--radius);
  font-size: 18px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: all var(--transition);
}
.plus-btn:hover:not(:disabled) {
  border-color: var(--border-hi);
  color: var(--text-1);
}
.plus-btn:disabled { opacity: 0.3; cursor: not-allowed; }

.memory-btn {
  width: 32px;
  height: 32px;
  background: var(--bg-active);
  border: 1px solid var(--border);
  color: var(--text-2);
  border-radius: var(--radius);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: all var(--transition);
  padding: 0;
}
.memory-btn:hover:not(:disabled) {
  border-color: var(--border-hi);
  color: var(--text-1);
}
.memory-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}
.memory-btn-longterm {
  border-style: dashed;
}
.memory-glyph {
  font-size: 16px;
  line-height: 1;
}

/* /ai/confirm 执行失败：与成功区分 */
.message.assistant.confirm-exec-error .msg-body {
  color: #f85149;
  border-left: 3px solid rgba(248, 81, 73, 0.65);
  padding-left: 10px;
}

/* 写入预览（preview_write） */
.msg-write-preview-card {
  margin-top: 10px;
  padding: 12px 14px;
  background: var(--bg-active);
  border: 1px solid var(--border-hi);
  border-radius: var(--radius, 8px);
  max-width: 100%;
}
.msg-write-preview-section {
  margin-top: 6px;
}
.msg-write-preview-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-3);
  letter-spacing: 0.04em;
  margin-bottom: 4px;
}
.msg-write-preview-label-sub {
  margin-top: 10px;
  font-weight: 500;
}
.msg-write-preview-body {
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-1);
  margin: 0 0 8px;
  white-space: pre-wrap;
}
.msg-write-preview-expert {
  margin-top: 4px;
  padding-top: 8px;
  border-top: 1px solid var(--border);
}
.msg-write-preview-expert-text {
  font-size: 12px;
  line-height: 1.55;
  color: var(--text-3);
  white-space: pre-wrap;
}
.msg-write-preview-meta {
  font-size: 11px;
  color: var(--text-3);
  margin: -6px 0 12px;
}
.msg-write-preview-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: flex-end;
}

/* 新建实体 — 待确认写入卡片（与 msg-plain-table 同系） */
.msg-confirm-card {
  margin-top: 4px;
  padding: 12px 14px;
  background: var(--bg-active);
  border: 1px solid var(--border-hi);
  border-radius: var(--radius);
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-width: 100%;
}
.msg-confirm-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-1);
  letter-spacing: 0.02em;
}
.msg-confirm-explanation {
  font-size: 12px;
  line-height: 1.65;
  color: var(--text-2);
}
.msg-confirm-subtitle {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-2);
  margin-top: 4px;
}
.msg-confirm-subtitle-items {
  margin-top: 12px;
}
.msg-confirm-table-wrap {
  max-height: 360px;
}
.msg-confirm-preview-table td.mono {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--accent);
}
.msg-confirm-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: flex-end;
  margin-top: 4px;
}
.confirm-write-btn {
  background: var(--accent);
  color: var(--bg);
  border: none;
  border-radius: var(--radius);
  font-size: 12px;
  padding: 7px 12px;
  cursor: pointer;
  transition: all var(--transition);
}
.confirm-write-btn:hover:not(:disabled) {
  background: var(--accent-hi);
}
.cancel-write-btn {
  background: var(--bg-hover);
  color: var(--text-2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  font-size: 12px;
  padding: 7px 12px;
  cursor: pointer;
  transition: all var(--transition);
}
.cancel-write-btn:hover:not(:disabled) {
  border-color: var(--border-hi);
  color: var(--text-1);
}
.confirm-write-btn:disabled,
.cancel-write-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.confirm-wrap {
  display: flex;
  justify-content: flex-end;
  margin-top: 8px;
}
.confirm-btn {
  background: var(--accent);
  color: var(--bg);
  border: none;
  border-radius: var(--radius);
  font-size: 12px;
  padding: 6px 10px;
  cursor: pointer;
  transition: all var(--transition);
}
.confirm-btn:hover:not(:disabled) { background: var(--accent-hi); }
.confirm-btn:disabled { opacity: 0.3; cursor: not-allowed; }
</style>
