<template>
  <div class="view">
    <div class="view-header">
      <div class="breadcrumb">
        <router-link :to="`/${schema}`" class="crumb">{{ schema }}</router-link>
        <span class="sep mono">/</span>
        <span class="crumb active">{{ splitCfg ? splitHeadline : tableTitle }}</span>
      </div>
      <div
        class="header-actions"
        v-if="!loading || colDefs.length > 0 || rows.length > 0"
      >
        <span class="row-count mono text-3">
          共 {{ pagination.itemCount }} 条 · 本页 {{ rows.length }} 行
        </span>
        <n-button
          size="small"
          tertiary
          :loading="exporting"
          :disabled="loading"
          @click="doExport"
        >
          导出 Excel
        </n-button>
        <n-button
          v-if="hasIdColumn && !splitCfg"
          size="small"
          tertiary
          :disabled="!hasIdColumn"
          @click="openAdd"
        >
          新增
        </n-button>
        <template v-if="splitCfg">
          <n-button
            v-if="hasIdColumn"
            size="small"
            tertiary
            @click="openAdd"
          >
            新增（主表）
          </n-button>
          <n-button
            v-if="childHasIdColumn"
            size="small"
            tertiary
            :disabled="selectedParentKey == null"
            @click="openAddChild"
          >
            新增（子表）
          </n-button>
        </template>
        <n-button
          size="small"
          tertiary
          :disabled="undoStack.length === 0"
          @click="doUndo"
        >
          撤销
        </n-button>
      </div>
    </div>

    <div v-if="!hasIdColumn && !loading" class="warn-banner text-3">
      该表无整数 <code>id</code> 列，暂不支持增删改与撤销。
    </div>

    <!-- 仅首屏/换表尚无列与行时全屏 loading；筛选/排序刷新时保留表格，避免表头输入框被卸载丢焦点 -->
    <div
      v-if="loading && !colDefs.length && !rows.length"
      class="loading-wrap"
    >
      <n-spin size="small" />
      <span class="text-3">加载中...</span>
    </div>

    <div
      v-else-if="rows.length > 0 || colDefs.length > 0"
      :class="splitCfg ? 'split-root' : 'table-wrap'"
      ref="tableWrapRef"
    >
      <!-- 单表 -->
      <template v-if="!splitCfg">
        <div class="filter-toolbar">
          <n-input
            :value="globalSearch"
            size="small"
            clearable
            placeholder="任意列模糊，回车搜索（与列头筛选 AND）"
            class="filter-global"
            @update:value="(v) => { globalSearch = v ?? '' }"
            @keydown.enter="doSearch"
          />
          <n-button size="tiny" tertiary @click="clearFilters">清空筛选</n-button>
        </div>
        <n-data-table
          remote
          :columns="columns"
          :data="rows"
          :row-key="rowKeyFn"
          :scroll-x="scrollXMin"
          :max-height="'calc(100vh - 220px)'"
          :bordered="false"
          :single-line="false"
          :pagination="pagination"
          size="small"
          striped
          @update:page="onPageChange"
          @update:page-size="onPageSizeChange"
          @update:sorter="onUpdateSorter"
        />
      </template>

      <!-- 主从分屏 -->
      <template v-else>
        <div class="split-pane split-pane--parent">
          <div class="split-pane-title text-3">
            主表 · {{ tableTitle }}
            <span class="mono split-hint">（点击行以筛选下方子表）</span>
          </div>
          <div class="filter-toolbar">
            <n-input
              :value="globalSearch"
              size="small"
              clearable
              placeholder="主表：任意列模糊，回车搜索"
              class="filter-global"
              @update:value="(v) => { globalSearch = v ?? '' }"
              @keydown.enter="doSearch"
            />
            <n-button size="tiny" tertiary @click="clearFilters">清空筛选</n-button>
          </div>
          <n-data-table
            remote
            :columns="columns"
            :data="rows"
            :row-key="rowKeyFn"
            :row-props="parentRowProps"
            :scroll-x="scrollXMin"
            :max-height="'min(42vh, 360px)'"
            :bordered="false"
            :single-line="false"
            :pagination="pagination"
            size="small"
            striped
            @update:page="onPageChange"
            @update:page-size="onPageSizeChange"
            @update:sorter="onUpdateSorter"
          />
        </div>
        <div class="split-divider" />
        <div class="split-pane split-pane--child" ref="childTableWrapRef">
          <div class="split-pane-title text-3">
            子表 · {{ childTitle }}
          </div>
          <div v-if="selectedParentKey == null" class="split-child-placeholder text-3">
            请在上表选择一行，子表将按关联字段筛选。
          </div>
          <template v-else>
            <div class="filter-toolbar">
              <n-input
                :value="childGlobalSearch"
                size="small"
                clearable
                placeholder="子表：任意列模糊，回车搜索（在已选主表范围内）"
                class="filter-global"
                @update:value="(v) => { childGlobalSearch = v ?? '' }"
                @keydown.enter="doChildSearch"
              />
              <n-button size="tiny" tertiary @click="clearChildFilters">清空</n-button>
            </div>
            <div v-if="childLoading && !childRows.length && !childColDefs.length" class="loading-wrap child-loading">
              <n-spin size="small" />
              <span class="text-3">加载子表…</span>
            </div>
            <n-data-table
              v-else
              remote
              :columns="childColumns"
              :data="childRows"
              :row-key="rowKeyFn"
              :scroll-x="childScrollXMin"
              :max-height="'min(38vh, 320px)'"
              :bordered="false"
              :single-line="false"
              :pagination="childPagination"
              size="small"
              striped
              @update:page="onChildPageChange"
              @update:page-size="onChildPageSizeChange"
              @update:sorter="onChildSorter"
            />
          </template>
        </div>
      </template>
    </div>

    <div v-else class="empty">
      <span class="mono text-3">{{ colDefs.length ? '— 该表暂无数据 —' : '— 无列定义（请同步 meta_fields）—' }}</span>
      <n-button v-if="hasIdColumn && colDefs.length" class="empty-add" size="small" @click="openAdd">新增一行</n-button>
    </div>

    <n-modal
      v-model:show="showModal"
      preset="card"
      :title="modalTitle"
      class="crud-modal"
      :style="{ width: 'min(640px, 92vw)' }"
      :mask-closable="false"
    >
      <n-scrollbar style="max-height: min(68vh, 520px); padding-right: 8px">
        <div class="form-stack">
          <n-form-item
            v-for="key in formKeys"
            :key="key"
            :label="labelOf(key)"
            label-placement="top"
            size="small"
          >
            <n-switch v-if="isBool(key)" v-model:value="formModel[key]" />
            <n-input
              v-else-if="isJson(key)"
              v-model:value="formModel[key]"
              type="textarea"
              :rows="4"
              placeholder="JSON"
            />
            <n-input
              v-else
              v-model:value="formModel[key]"
              :placeholder="key"
              :disabled="modalMode === 'edit' && key === 'id'"
            />
          </n-form-item>
        </div>
      </n-scrollbar>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showModal = false">取消</n-button>
          <n-button type="primary" :loading="saving" @click="submitForm">保存</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<script setup>
import {
  ref,
  reactive,
  computed,
  watch,
  h,
  nextTick,
  onBeforeUnmount,
  defineComponent,
  toRaw,
  provide,
  inject,
} from 'vue'
import { useRoute } from 'vue-router'
import { useAppStore } from '../stores/app'
import { buildExportXlsxUrl } from '../utils/tableExport'
import { splitConfigForRouteTable } from '../config/tableSplit'
import Sortable from 'sortablejs'
import {
  NDataTable,
  NSpin,
  NButton,
  NModal,
  NFormItem,
  NInput,
  NSwitch,
  NScrollbar,
  NSpace,
  createDiscreteApi,
} from 'naive-ui'

const { message, dialog } = createDiscreteApi(['message', 'dialog'])

/** 清空筛选 / 换表时通知列头输入框重置本地文案（columns 不因 columnFilters 重算） */
const FILTER_CLEAR_GEN_KEY = Symbol('aierpTableFilterClearGen')

/**
 * 独立组件：列头筛选输入框
 * 避免在输入时整个 columns computed 重新渲染导致输入框失焦
 */
const HeaderFilterInput = defineComponent({
  props: {
    columnKey: { type: String, required: true },
    initialValue: { type: String, default: '' },
  },
  emits: ['update'],
  setup(props, { emit }) {
    const localValue = ref(props.initialValue)
    const clearGen = inject(FILTER_CLEAR_GEN_KEY, null)

    const commit = () => {
      emit('update', props.columnKey, localValue.value)
    }

    watch(() => props.initialValue, (newVal) => {
      localValue.value = newVal
    })

    if (clearGen) {
      watch(clearGen, () => {
        localValue.value = ''
      })
    }

    const stopHead = (e) => e.stopPropagation()

    return () =>
      h(
        'div',
        {
          class: 'th-filter-wrap',
          onMousedown: stopHead,
          onClick: stopHead,
          onPointerdown: stopHead,
        },
        [
          h(NInput, {
            class: 'th-filter',
            size: 'tiny',
            placeholder: '回车搜索',
            clearable: true,
            value: localValue.value,
            onUpdateValue: (v) => {
              localValue.value = v ?? ''
            },
            onKeydown: (e) => {
              if (e.key === 'Enter') commit()
            },
          }),
        ]
      )
  },
})

const route = useRoute()
const store = useAppStore()

const filterClearGeneration = ref(0)
provide(FILTER_CLEAR_GEN_KEY, filterClearGeneration)

const schema = computed(() => (route.path.startsWith('/meta') ? 'meta' : 'business'))
const tableName = computed(() => route.params.table)

/** 主从分屏：路由为父表或子表名均可 */
const splitCfg = computed(() => splitConfigForRouteTable(String(tableName.value || '')))
const displaySchema = computed(() => splitCfg.value?.parent.schema ?? schema.value)
const displayTable = computed(() => splitCfg.value?.parent.table ?? tableName.value)
const childSchema = computed(() => splitCfg.value?.child.schema ?? '')
const childTable = computed(() => splitCfg.value?.child.table ?? '')
const childFk = computed(() => splitCfg.value?.child.fk ?? '')
const parentKeyCol = computed(() => splitCfg.value?.parent.key ?? 'id')

const selectedParentKey = ref(null)
const crudTarget = ref('parent')

const rows = ref([])
const colDefs = ref([])
const loading = ref(false)
const showModal = ref(false)
const modalMode = ref('add')
const formModel = ref({})
const saving = ref(false)
const exporting = ref(false)
/** 任意列 OR 模糊 */
const globalSearch = ref('')
/** 列名 → 单列模糊，多列 AND */
const columnFilters = reactive({})

/** 服务端排序 + 分页：与 naive remote 表格联动 */
const sortBy = ref('id')
const sortOrder = ref('desc')
const pagination = reactive({
  page: 1,
  pageSize: 500,
  itemCount: 0,
  showSizePicker: true,
  pageSizes: [50, 100, 200, 500],
})
const editSnapshot = ref(null)
const undoStack = ref([])
const childRows = ref([])
const childColDefs = ref([])
const childLoading = ref(false)
const childGlobalSearch = ref('')
const childColumnFilters = reactive({})
const childSortBy = ref('id')
const childSortOrder = ref('desc')
const childPagination = reactive({
  page: 1,
  pageSize: 200,
  itemCount: 0,
  showSizePicker: true,
  pageSizes: [50, 100, 200, 500],
})
const tableWrapRef = ref(null)
const childTableWrapRef = ref(null)
/** 用户拖拽后的列顺序；null 表示跟随 meta_fields 默认顺序 */
const manualOrder = ref(null)
let headerSortable = null

const BLOCK_FORM = new Set(['embedding', 'password_hash', 'intent_embedding'])

const tableTitle = computed(() => {
  const n = splitCfg.value ? displayTable.value : tableName.value
  const all = [...store.nav.design, ...store.nav.prep, ...store.nav.run]
  const hit = all.find((t) => t.table_name === n)
  return hit?.table_cn || n
})

const childTitle = computed(() => {
  if (!splitCfg.value) return ''
  const all = [...store.nav.design, ...store.nav.prep, ...store.nav.run]
  const hit = all.find((t) => t.table_name === childTable.value)
  return hit?.table_cn || childTable.value
})

const splitHeadline = computed(() => {
  if (!splitCfg.value) return ''
  return `${tableTitle.value} → ${childTitle.value}`
})

function unionRowKeys(rowsArr) {
  const s = new Set()
  rowsArr.forEach((row) => {
    if (row && typeof row === 'object') Object.keys(row).forEach((k) => s.add(k))
  })
  return [...s]
}

const orderedFieldKeys = computed(() => {
  const defs = colDefs.value
  const fromMeta = defs.map((d) => d.field_name)
  const fromRows = unionRowKeys(rows.value)
  const extra = fromRows.filter((k) => !fromMeta.includes(k))
  extra.sort()
  if (fromMeta.length) return [...fromMeta, ...extra]
  return fromRows.sort()
})

function mergeColumnOrder(saved, canonical) {
  if (!saved?.length) return [...canonical]
  const set = new Set(canonical)
  const out = saved.filter((k) => set.has(k))
  for (const k of canonical) {
    if (!out.includes(k)) out.push(k)
  }
  return out
}

const effectiveDataKeys = computed(() =>
  mergeColumnOrder(manualOrder.value, orderedFieldKeys.value)
)

const childOrderedFieldKeys = computed(() => {
  const defs = childColDefs.value
  const fromMeta = defs.map((d) => d.field_name)
  const fromRows = unionRowKeys(childRows.value)
  const extra = fromRows.filter((k) => !fromMeta.includes(k))
  extra.sort()
  if (fromMeta.length) return [...fromMeta, ...extra]
  return fromRows.sort()
})

const hasIdColumn = computed(() => orderedFieldKeys.value.includes('id'))
const childHasIdColumn = computed(() => childOrderedFieldKeys.value.includes('id'))

const formKeys = computed(() => {
  let keys
  if (showModal.value && crudTarget.value === 'child') {
    const defs = childColDefs.value
    const fromMeta = defs.map((d) => d.field_name)
    const fromRows = unionRowKeys(childRows.value)
    const extra = fromRows.filter((k) => !fromMeta.includes(k))
    extra.sort()
    keys = fromMeta.length ? [...fromMeta, ...extra] : fromRows.sort()
  } else {
    keys = effectiveDataKeys.value
  }
  return keys.filter((k) => !BLOCK_FORM.has(k))
})

const scrollXMin = computed(() => {
  const n = effectiveDataKeys.value.length
  return Math.max(800, n * 124 + (hasIdColumn.value ? 120 : 0))
})

function loadStoredColumnOrder() {
  try {
    const raw = localStorage.getItem(
      `aierp:colOrder:${displaySchema.value}.${displayTable.value}`
    )
    if (!raw) return null
    const arr = JSON.parse(raw)
    return Array.isArray(arr) ? arr : null
  } catch {
    return null
  }
}

function destroyHeaderSortable() {
  headerSortable?.destroy()
  headerSortable = null
}

function bindHeaderSortable() {
  destroyHeaderSortable()
  if (!hasIdColumn.value || loading.value) return
  const wrap = tableWrapRef.value
  if (!wrap) return
  const tr = wrap.querySelector('thead tr')
  if (!tr || tr.children.length < 2) return

  headerSortable = Sortable.create(tr, {
    animation: 160,
    handle: '.th-drag-handle',
    filter: 'th:first-child',
    preventOnFilter: true,
    onMove(evt) {
      const rel = evt.related
      if (!rel) return true
      const idx = [...tr.children].indexOf(rel)
      if (idx === 0 && evt.willInsertAfter === false) return false
      return true
    },
    onEnd(evt) {
      if (evt.oldIndex === evt.newIndex) return
      const off = 1
      if (evt.oldIndex < off || evt.newIndex < off) return
      const keys = [...effectiveDataKeys.value]
      const from = evt.oldIndex - off
      const to = evt.newIndex - off
      if (from < 0 || from >= keys.length) return
      if (to < 0 || to > keys.length) return
      const next = [...keys]
      const [moved] = next.splice(from, 1)
      next.splice(to, 0, moved)
      manualOrder.value = next
      localStorage.setItem(
        `aierp:colOrder:${displaySchema.value}.${displayTable.value}`,
        JSON.stringify(next)
      )
    },
  })
}

const modalTitle = computed(() => {
  const tit =
    crudTarget.value === 'child' ? childTitle.value : tableTitle.value
  return modalMode.value === 'add' ? `新增 · ${tit}` : `编辑 · ${tit}`
})

function fieldMeta(key) {
  const defs =
    showModal.value && crudTarget.value === 'child'
      ? childColDefs.value
      : colDefs.value
  return defs.find((c) => c.field_name === key)
}

function labelOf(key) {
  return fieldMeta(key)?.menu_label_cn || key
}

function isBool(key) {
  return (fieldMeta(key)?.data_type || '').toLowerCase().includes('bool')
}

function isJson(key) {
  const d = (fieldMeta(key)?.data_type || '').toLowerCase()
  return d.includes('json')
}

function rowKeyFn(row) {
  return row?.id ?? JSON.stringify(row)
}

function parentRowProps(row) {
  if (!splitCfg.value) return undefined
  const k = row[parentKeyCol.value]
  const sel =
    selectedParentKey.value != null &&
    String(k) === String(selectedParentKey.value)
  return {
    style: 'cursor: pointer;',
    class: sel ? 'split-parent-row--selected' : '',
    onClick: () => {
      selectedParentKey.value = k == null ? null : k
      childPagination.page = 1
      loadChild()
    },
  }
}

function childLabelOf(key) {
  return (
    childColDefs.value.find((c) => c.field_name === key)?.menu_label_cn || key
  )
}

function rowToForm(row) {
  const o = {}
  for (const key of formKeys.value) {
    let v = row[key]
    if (v === null || v === undefined) {
      o[key] = isBool(key) ? false : ''
      continue
    }
    if (typeof v === 'object' && !Array.isArray(v) && !(v instanceof Date)) {
      o[key] = JSON.stringify(v, null, 2)
      continue
    }
    if (Array.isArray(v)) {
      o[key] = JSON.stringify(v, null, 2)
      continue
    }
    if (typeof v === 'boolean') {
      o[key] = v
      continue
    }
    o[key] = String(v)
  }
  return o
}

function emptyForm() {
  const o = {}
  for (const key of formKeys.value) {
    if (key === 'id') {
      o[key] = ''
      continue
    }
    o[key] = isBool(key) ? false : ''
  }
  return o
}

function parseValue(raw, dataType, key) {
  const dt = (dataType || '').toLowerCase()
  if (raw === '' || raw === null || raw === undefined) {
    if (isBool(key)) return false
    return null
  }
  if (typeof raw === 'boolean') return raw
  if (dt.includes('bool')) {
    return raw === true || raw === 'true' || raw === '1' || raw === 1
  }
  if (
    dt.includes('int') ||
    dt.includes('numeric') ||
    dt.includes('double') ||
    dt.includes('real') ||
    dt.includes('decimal') ||
    dt.includes('serial')
  ) {
    const n = Number(raw)
    if (Number.isNaN(n)) throw new Error(`字段 ${key} 需要数字`)
    return n
  }
  if (dt.includes('json')) {
    try {
      return JSON.parse(String(raw))
    } catch {
      throw new Error(`字段 ${key} JSON 无效`)
    }
  }
  return raw
}

function buildPayload() {
  const payload = {}
  for (const key of formKeys.value) {
    if (key === 'id' && modalMode.value === 'edit') continue
    if (modalMode.value === 'add' && key === 'id') {
      const v = formModel.value[key]
      if (v === '' || v === null || v === undefined) continue
    }
    const meta = fieldMeta(key)
    payload[key] = parseValue(formModel.value[key], meta?.data_type, key)
  }
  return payload
}

function pushUndo(entry) {
  undoStack.value.push(entry)
  if (undoStack.value.length > 40) undoStack.value.shift()
}

function openAdd() {
  if (!hasIdColumn.value) return
  crudTarget.value = 'parent'
  modalMode.value = 'add'
  editSnapshot.value = null
  formModel.value = emptyForm()
  showModal.value = true
}

async function openAddChild() {
  if (!splitCfg.value || !childHasIdColumn.value) return
  if (selectedParentKey.value == null) {
    message.warning('请先在上表选择一行（主表记录）')
    return
  }
  crudTarget.value = 'child'
  modalMode.value = 'add'
  editSnapshot.value = null
  showModal.value = true
  await nextTick()
  formModel.value = emptyForm()
  const fk = childFk.value
  if (fk) {
    formModel.value[fk] = String(selectedParentKey.value)
  }
}

function openEdit(row) {
  if (!hasIdColumn.value || row.id == null) return
  crudTarget.value = 'parent'
  modalMode.value = 'edit'
  editSnapshot.value = JSON.parse(JSON.stringify(row))
  formModel.value = rowToForm(row)
  showModal.value = true
}

async function openEditChild(row) {
  if (!childHasIdColumn.value || row.id == null) return
  crudTarget.value = 'child'
  modalMode.value = 'edit'
  editSnapshot.value = JSON.parse(JSON.stringify(row))
  showModal.value = true
  await nextTick()
  formModel.value = rowToForm(row)
}

async function submitForm() {
  let payload
  try {
    payload = buildPayload()
  } catch (e) {
    message.error(e.message || String(e))
    return
  }
  const sch =
    crudTarget.value === 'child' ? childSchema.value : displaySchema.value
  const tbl =
    crudTarget.value === 'child' ? childTable.value : displayTable.value
  saving.value = true
  try {
    if (modalMode.value === 'add') {
      const { row } = await store.tableInsert(sch, tbl, payload)
      if (crudTarget.value === 'parent') {
        pushUndo({ type: 'insert', id: row.id })
      }
      message.success('已新增')
    } else {
      const id = editSnapshot.value?.id
      const { row } = await store.tableUpdate(sch, tbl, id, payload)
      if (crudTarget.value === 'parent') {
        pushUndo({ type: 'update', id, before: editSnapshot.value })
      }
      message.success('已保存')
    }
    showModal.value = false
    await load()
    if (splitCfg.value) await loadChild()
  } catch (e) {
    message.error(e.message || String(e))
  } finally {
    saving.value = false
  }
}

function onDeleteRow(row) {
  if (row.id == null) return
  dialog.warning({
    title: '确认删除',
    content: `确定删除 id = ${row.id} ？`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await store.tableDelete(displaySchema.value, displayTable.value, row.id)
        pushUndo({ type: 'delete', row: JSON.parse(JSON.stringify(row)) })
        message.success('已删除')
        await load()
        if (splitCfg.value) await loadChild()
      } catch (e) {
        message.error(e.message || String(e))
      }
    },
  })
}

function onDeleteChildRow(row) {
  if (row.id == null) return
  dialog.warning({
    title: '确认删除',
    content: `确定删除子表 id = ${row.id} ？`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await store.tableDelete(childSchema.value, childTable.value, row.id)
        message.success('已删除')
        await loadChild()
      } catch (e) {
        message.error(e.message || String(e))
      }
    },
  })
}

async function doUndo() {
  const e = undoStack.value.pop()
  if (!e) return
  try {
    if (e.type === 'insert') {
      await store.tableDelete(displaySchema.value, displayTable.value, e.id)
      message.success('已撤销新增')
    } else if (e.type === 'delete') {
      await store.tableInsert(displaySchema.value, displayTable.value, e.row)
      message.success('已撤销删除')
    } else if (e.type === 'update') {
      const payload = { ...e.before }
      delete payload.id
      await store.tableUpdate(
        displaySchema.value,
        displayTable.value,
        e.id,
        payload
      )
      message.success('已撤销修改')
    }
    await load()
    if (splitCfg.value) await loadChild()
  } catch (err) {
    message.error(err.message || String(err))
    undoStack.value.push(e)
  }
}

function doSearch() {
  pagination.page = 1
  load()
}

function buildDataColumn(key) {
  const showDrag = hasIdColumn.value
  return {
    title: () =>
      h('div', { class: 'th-stack' }, [
        h('div', { class: 'th-label' }, [
          showDrag
            ? h(
                'span',
                { class: 'th-drag-handle', title: '拖动调整列顺序' },
                '⋮⋮'
              )
            : null,
          h('span', { class: 'th-text' }, labelOf(key)),
        ]),
        h(HeaderFilterInput, {
          columnKey: key,
          initialValue: toRaw(columnFilters)[key] ?? '',
          onUpdate: (k, v) => {
            columnFilters[k] = v
            doSearch()
          },
        }),
      ]),
    key,
    sorter: true,
    ellipsis: { tooltip: true },
    minWidth: 112,
    width: 140,
    render(row) {
      const val = row[key]
      if (val === null || val === undefined) {
        return h(
          'span',
          { style: 'color: rgba(0,0,0,0.45); font-style: italic;' },
          'null'
        )
      }
      const str = typeof val === 'object' ? JSON.stringify(val) : String(val)
      return h('span', { class: 'cell-val' }, str)
    },
  }
}

// 列 key 字符串快照，只在列结构变化时触发 columns 重算，避免 rows 更新导致表头重渲染
const _colKeysSnapshot = computed(() => effectiveDataKeys.value.join('|'))

const columns = computed(() => {
  const keysStr = _colKeysSnapshot.value
  // 列筛选与排序状态不纳入依赖，避免重算列定义、重建表头筛选框
  const keys = keysStr ? keysStr.split('|') : []
  const dataCols = keys.map((key) => buildDataColumn(key))

  if (!hasIdColumn.value) return dataCols

  const actionCol = {
    title: () =>
      h('div', { class: 'th-stack' }, [
        h('div', { class: 'th-label th-label--action' }, '操作'),
        h('div', { class: 'th-filter-spacer' }),
      ]),
    key: '_actions',
    width: 120,
    fixed: 'left',
    render(row) {
      return h(
        'div',
        { class: 'act-cell' },
        [
          h(
            NButton,
            {
              size: 'tiny',
              tertiary: true,
              onClick: () => openEdit(row),
            },
            () => '编辑'
          ),
          h(
            NButton,
            {
              size: 'tiny',
              tertiary: true,
              type: 'error',
              onClick: () => onDeleteRow(row),
            },
            () => '删除'
          ),
        ]
      )
    },
  }
  return [actionCol, ...dataCols]
})

function buildChildDataColumn(key) {
  return {
    title: () =>
      h('div', { class: 'th-stack' }, [
        h('div', { class: 'th-label' }, [
          h('span', { class: 'th-text' }, childLabelOf(key)),
        ]),
      ]),
    key,
    sorter: true,
    ellipsis: { tooltip: true },
    minWidth: 112,
    width: 140,
    render(row) {
      const val = row[key]
      if (val === null || val === undefined) {
        return h(
          'span',
          { style: 'color: rgba(0,0,0,0.45); font-style: italic;' },
          'null'
        )
      }
      const str = typeof val === 'object' ? JSON.stringify(val) : String(val)
      return h('span', { class: 'cell-val' }, str)
    },
  }
}

const _childColKeysSnapshot = computed(() => childOrderedFieldKeys.value.join('|'))

const childColumns = computed(() => {
  const keysStr = _childColKeysSnapshot.value
  const keys = keysStr ? keysStr.split('|') : []
  const dataCols = keys.map((key) => buildChildDataColumn(key))
  if (!childHasIdColumn.value) return dataCols
  const actionCol = {
    title: () =>
      h('div', { class: 'th-stack' }, [
        h('div', { class: 'th-label th-label--action' }, '操作'),
      ]),
    key: '_child_actions',
    width: 120,
    fixed: 'left',
    render(row) {
      return h('div', { class: 'act-cell' }, [
        h(
          NButton,
          {
            size: 'tiny',
            tertiary: true,
            onClick: () => openEditChild(row),
          },
          () => '编辑'
        ),
        h(
          NButton,
          {
            size: 'tiny',
            tertiary: true,
            type: 'error',
            onClick: () => onDeleteChildRow(row),
          },
          () => '删除'
        ),
      ])
    },
  }
  return [actionCol, ...dataCols]
})

const childScrollXMin = computed(() => {
  const n = childOrderedFieldKeys.value.length
  return Math.max(640, n * 120 + (childHasIdColumn.value ? 120 : 0))
})

const EXPORT_ROW_CAP = 10000

function buildFilterPayload() {
  const f = {}
  for (const k of effectiveDataKeys.value) {
    const v = String(columnFilters[k] ?? '').trim()
    if (v) f[k] = v
  }
  const qq = globalSearch.value.trim()
  return {
    filters: Object.keys(f).length ? f : undefined,
    q: qq || undefined,
  }
}

function clearFilters() {
  globalSearch.value = ''
  for (const k of Object.keys(columnFilters)) {
    columnFilters[k] = ''
  }
  filterClearGeneration.value++
  pagination.page = 1
  load()
}

function onPageChange(p) {
  pagination.page = p
  load()
}

function onPageSizeChange(ps) {
  pagination.pageSize = ps
  pagination.page = 1
  load()
}

function onUpdateSorter(s) {
  const st = Array.isArray(s) ? s[0] : s
  if (!st || st.order === false) {
    sortBy.value = null
    sortOrder.value = null
    pagination.page = 1
    load()
    return
  }
  if (st.columnKey === '_actions') return
  sortBy.value = String(st.columnKey)
  sortOrder.value = st.order === 'ascend' ? 'asc' : 'desc'
  pagination.page = 1
  load()
}

function doExport() {
  exporting.value = true
  try {
    const url = buildExportXlsxUrl(
      displaySchema.value,
      displayTable.value,
      EXPORT_ROW_CAP,
      effectiveDataKeys.value,
      {
        ...buildFilterPayload(),
        sortBy: sortBy.value || undefined,
        sortOrder: sortOrder.value || undefined,
      }
    )
    // 必须在点击回调里同步打开链接：await fetch 后再 programmatic click 会被多数浏览器拦截下载
    const a = document.createElement('a')
    a.href = url
    a.target = '_blank'
    a.rel = 'noopener noreferrer'
    document.body.appendChild(a)
    a.click()
    a.remove()
    message.success('已开始下载')
  } catch (e) {
    message.error(e.message || String(e))
  } finally {
    exporting.value = false
  }
}

async function loadChild() {
  if (!splitCfg.value) return
  if (selectedParentKey.value == null) {
    childRows.value = []
    childColDefs.value = []
    childPagination.itemCount = 0
    childLoading.value = false
    return
  }
  childLoading.value = true
  try {
    const lim = childPagination.pageSize
    const off = (childPagination.page - 1) * lim
    const fk = childFk.value
    const filters = { [fk]: String(selectedParentKey.value) }
    const qq = childGlobalSearch.value.trim()
    const [data, labels] = await Promise.all([
      store.fetchTableData(childSchema.value, childTable.value, lim, {
        filters,
        offset: off,
        sortBy: childSortBy.value || undefined,
        sortOrder: childSortOrder.value || undefined,
        q: qq || undefined,
      }),
      store.fetchColumnLabels(childTable.value),
    ])
    childRows.value = data.rows || []
    childColDefs.value = labels.columns || []
    childPagination.itemCount =
      typeof data.total === 'number' ? data.total : childRows.value.length
    const maxPage = Math.max(
      1,
      Math.ceil(childPagination.itemCount / childPagination.pageSize) || 1
    )
    if (childPagination.page > maxPage) {
      childPagination.page = maxPage
      return loadChild()
    }
    await nextTick()
    if (
      childSortBy.value != null &&
      !childOrderedFieldKeys.value.includes(childSortBy.value)
    ) {
      childSortBy.value = childOrderedFieldKeys.value.includes('id')
        ? 'id'
        : childOrderedFieldKeys.value[0] || null
      childSortOrder.value = childSortBy.value === 'id' ? 'desc' : 'asc'
    }
  } finally {
    childLoading.value = false
  }
}

async function load() {
  loading.value = true
  rows.value = []
  const lim = pagination.pageSize
  const off = (pagination.page - 1) * lim
  const [data, labels] = await Promise.all([
    store.fetchTableData(displaySchema.value, displayTable.value, lim, {
      ...buildFilterPayload(),
      offset: off,
      sortBy: sortBy.value || undefined,
      sortOrder: sortOrder.value || undefined,
    }),
    store.fetchColumnLabels(displayTable.value),
  ])
  if (!store.nav.design?.length && !store.nav.prep?.length && !store.nav.run?.length) {
    await store.fetchNavTables()
  }
  rows.value = data.rows || []
  colDefs.value = labels.columns || []
  pagination.itemCount =
    typeof data.total === 'number' ? data.total : rows.value.length
  const maxPage = Math.max(
    1,
    Math.ceil(pagination.itemCount / pagination.pageSize) || 1
  )
  if (pagination.page > maxPage) {
    pagination.page = maxPage
    loading.value = false
    return load()
  }
  await nextTick()
  if (
    sortBy.value != null &&
    !orderedFieldKeys.value.includes(sortBy.value)
  ) {
    sortBy.value = orderedFieldKeys.value.includes('id')
      ? 'id'
      : orderedFieldKeys.value[0] || null
    sortOrder.value = sortBy.value === 'id' ? 'desc' : 'asc'
  }
  loading.value = false
  if (splitCfg.value && selectedParentKey.value != null) {
    await loadChild()
  }
}

function doChildSearch() {
  childPagination.page = 1
  loadChild()
}

function onChildPageChange(p) {
  childPagination.page = p
  loadChild()
}

function onChildPageSizeChange(ps) {
  childPagination.pageSize = ps
  childPagination.page = 1
  loadChild()
}

function onChildSorter(s) {
  const st = Array.isArray(s) ? s[0] : s
  if (!st || st.order === false) {
    childSortBy.value = null
    childSortOrder.value = null
    childPagination.page = 1
    loadChild()
    return
  }
  if (st.columnKey === '_child_actions') return
  childSortBy.value = String(st.columnKey)
  childSortOrder.value = st.order === 'ascend' ? 'asc' : 'desc'
  childPagination.page = 1
  loadChild()
}

function clearChildFilters() {
  childGlobalSearch.value = ''
  childPagination.page = 1
  loadChild()
}

watch(
  effectiveDataKeys,
  (keys) => {
    const set = new Set(keys)
    for (const k of Object.keys(columnFilters)) {
      if (!set.has(k)) delete columnFilters[k]
    }
    for (const k of keys) {
      if (!(k in columnFilters)) columnFilters[k] = ''
    }
  },
  { immediate: true }
)

watch(
  [schema, tableName],
  () => {
    undoStack.value = []
    selectedParentKey.value = null
    crudTarget.value = 'parent'
    manualOrder.value = loadStoredColumnOrder()
    globalSearch.value = ''
    for (const k of Object.keys(columnFilters)) delete columnFilters[k]
    filterClearGeneration.value++
    childGlobalSearch.value = ''
    childRows.value = []
    childColDefs.value = []
    childPagination.page = 1
    childPagination.pageSize = 200
    childPagination.itemCount = 0
    childSortBy.value = 'id'
    childSortOrder.value = 'desc'
    rows.value = []
    colDefs.value = []
    pagination.page = 1
    pagination.pageSize = 500
    pagination.itemCount = 0
    sortBy.value = 'id'
    sortOrder.value = 'desc'
    load()
  },
  { immediate: true }
)

watch(
  () => [
    loading.value,
    manualOrder.value?.join?.(',') ?? '',
    orderedFieldKeys.value.join(','),
    hasIdColumn.value,
    tableName.value,
  ],
  async () => {
    await nextTick()
    destroyHeaderSortable()
    bindHeaderSortable()
  },
  { flush: 'post' }
)

onBeforeUnmount(() => {
  destroyHeaderSortable()
})
</script>

<style scoped>
.view {
  padding: 24px 28px;
  height: 100vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.view-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  gap: 12px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.breadcrumb {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.crumb {
  font-size: 14px;
  color: var(--text-3);
  text-decoration: none;
  transition: color var(--transition);
}
.crumb:hover {
  color: var(--text-2);
}
.crumb.active {
  color: var(--text-1);
  font-size: 16px;
  font-weight: 400;
}
.sep {
  color: var(--text-3);
}
.row-count {
  font-size: 12px;
  margin-right: 4px;
}

.warn-banner {
  font-size: 12px;
  padding: 8px 12px;
  margin-bottom: 12px;
  background: var(--bg-active);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}
.warn-banner code {
  font-family: var(--font-mono);
  font-size: 11px;
}

.loading-wrap {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: var(--text-3);
  font-size: 13px;
}

.table-wrap {
  flex: 1;
  overflow: hidden;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.filter-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}
.filter-global {
  max-width: min(360px, 100%);
  flex: 1;
}

.th-stack {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 4px;
  width: 100%;
  min-width: 0;
}
.th-label--action {
  min-height: 22px;
  display: flex;
  align-items: center;
}
.th-filter-spacer {
  min-height: 28px;
  flex-shrink: 0;
}
.th-filter-wrap {
  width: 100%;
  min-width: 0;
}
:deep(.th-filter.n-input) {
  font-size: 11px;
}
:deep(.th-filter .n-input__input-el) {
  font-size: 11px;
}

.empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  font-size: 13px;
}

.form-stack {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

:deep(.n-data-table) {
  background: transparent;
}
:deep(.n-data-table-thead .n-data-table-th) {
  background: var(--bg-active);
  border-bottom: 1px solid var(--border-hi);
  font-family: var(--font-body);
  font-size: 12px;
  color: var(--text-1);
  letter-spacing: 0.02em;
  font-weight: 600;
}
:deep(.n-data-table-td) {
  border-bottom: 1px solid var(--border);
  font-size: 12px;
  color: var(--text-1) !important;
}
:deep(.n-data-table-tr:not(.n-data-table-tr--striped) .n-data-table-td) {
  background: var(--bg-panel) !important;
}
:deep(.n-data-table-tr--striped .n-data-table-td) {
  background: var(--bg-hover) !important;
}
:deep(.n-data-table-tr:not(.n-data-table-tr--striped):hover .n-data-table-td) {
  background: var(--bg-hover) !important;
}
:deep(.n-data-table-tr--striped:hover .n-data-table-td) {
  background: var(--bg-active) !important;
}

.cell-val {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-1);
}

/* 表头筛选 / 弹窗输入：与全局浅色主题一致 */
:deep(.filter-global .n-input__input-el),
:deep(.filter-global .n-input__textarea-el),
:deep(.th-filter .n-input__input-el),
:deep(.th-filter .n-input__textarea-el),
:deep(.crud-modal .n-input__input-el),
:deep(.crud-modal .n-input__textarea-el) {
  color: var(--text-1) !important;
  caret-color: var(--text-1) !important;
}

:deep(.filter-global .n-input__placeholder),
:deep(.th-filter .n-input__placeholder),
:deep(.crud-modal .n-input__placeholder) {
  color: var(--text-3) !important;
}

:deep(.filter-global .n-input__input-el),
:deep(.th-filter .n-input__input-el),
:deep(.crud-modal .n-input__input-el) {
  background: var(--bg-panel) !important;
}

:deep(.filter-global .n-input__textarea-el),
:deep(.th-filter .n-input__textarea-el),
:deep(.crud-modal .n-input__textarea-el) {
  background: var(--bg-panel) !important;
}

:deep(.crud-modal input),
:deep(.crud-modal textarea),
:deep(.th-filter input),
:deep(.th-filter textarea) {
  color: var(--text-1) !important;
  caret-color: var(--text-1) !important;
  background: var(--bg-panel) !important;
}

.act-cell {
  display: flex;
  gap: 4px;
  flex-wrap: nowrap;
  align-items: center;
}

.th-label {
  display: inline-flex;
  align-items: center;
  gap: 0;
  min-width: 0;
}
.th-drag-handle {
  cursor: grab;
  display: inline-block;
  margin-right: 6px;
  padding: 0 2px;
  opacity: 0.55;
  font-size: 9px;
  line-height: 1;
  letter-spacing: -1px;
  user-select: none;
  color: var(--text-2);
}
.th-drag-handle:active {
  cursor: grabbing;
}
.th-text {
  flex: 1;
  min-width: 0;
}

.split-root {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 0;
  overflow: hidden;
}

.split-pane {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 0;
  flex: 1 1 50%;
  overflow: hidden;
}

.split-pane-title {
  font-size: 12px;
  letter-spacing: 0.04em;
  flex-shrink: 0;
}

.split-hint {
  font-size: 11px;
  color: var(--text-3);
  font-weight: 400;
}

.split-divider {
  flex-shrink: 0;
  height: 1px;
  background: var(--border-hi);
  margin: 4px 0;
}

.split-child-placeholder {
  padding: 20px;
  text-align: center;
  border: 1px dashed var(--border);
  border-radius: var(--radius);
}

.child-loading {
  min-height: 120px;
}

:deep(.split-parent-row--selected td) {
  background: var(--bg-active) !important;
  box-shadow: inset 3px 0 0 0 var(--text-2);
}
</style>
