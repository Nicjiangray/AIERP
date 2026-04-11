-- 侧栏：仅主表（子表不出导航）；「系统准备」含联系人；「系统运行」顺序：
--   contracts（合同管理）→ contract_changes → purchase_orders → payments → receiving_notes → shipping_notes → inventory
--   其后：approvals（若存在 meta 行）
-- 执行：psql -U nicjiang -d aierp -v ON_ERROR_STOP=1 -f constitution/v26_nav_run_order_main_only.sql

BEGIN;

-- 子表：不参与侧栏
-- nav_order 列 NOT NULL 时不能置空；无导航用 0 占位
UPDATE meta.meta_tables SET
  nav_section = NULL,
  nav_order = 0,
  nav_parent_table = NULL
WHERE table_name IN (
  'contract_items',
  'purchase_order_items',
  'receiving_note_items',
  'shipping_note_items',
  'payment_allocations',
  'workflow_steps'
);

-- 系统准备：联系人
UPDATE meta.meta_tables SET
  table_cn = COALESCE(NULLIF(TRIM(table_cn), ''), '联系人'),
  nav_section = 'prep',
  nav_order = 11,
  nav_parent_table = NULL
WHERE table_name = 'contacts';

-- 系统运行（主流程；合同管理置顶）
UPDATE meta.meta_tables SET
  table_cn = COALESCE(NULLIF(TRIM(table_cn), ''), '合同管理'),
  nav_section = 'run',
  nav_order = 10,
  nav_parent_table = NULL
WHERE table_name = 'contracts';

UPDATE meta.meta_tables SET
  table_cn = COALESCE(NULLIF(TRIM(table_cn), ''), '合同变更'),
  nav_section = 'run',
  nav_order = 20,
  nav_parent_table = NULL
WHERE table_name = 'contract_changes';

UPDATE meta.meta_tables SET
  table_cn = COALESCE(NULLIF(TRIM(table_cn), ''), '采购订单'),
  nav_section = 'run',
  nav_order = 30,
  nav_parent_table = NULL
WHERE table_name = 'purchase_orders';

UPDATE meta.meta_tables SET
  table_cn = COALESCE(NULLIF(TRIM(table_cn), ''), '收付款'),
  nav_section = 'run',
  nav_order = 40,
  nav_parent_table = NULL
WHERE table_name = 'payments';

UPDATE meta.meta_tables SET
  table_cn = COALESCE(NULLIF(TRIM(table_cn), ''), '到货单'),
  nav_section = 'run',
  nav_order = 50,
  nav_parent_table = NULL
WHERE table_name = 'receiving_notes';

UPDATE meta.meta_tables SET
  table_cn = COALESCE(NULLIF(TRIM(table_cn), ''), '发货单'),
  nav_section = 'run',
  nav_order = 60,
  nav_parent_table = NULL
WHERE table_name = 'shipping_notes';

UPDATE meta.meta_tables SET
  table_cn = COALESCE(NULLIF(TRIM(table_cn), ''), '库存批次'),
  nav_section = 'run',
  nav_order = 70,
  nav_parent_table = NULL
WHERE table_name = 'inventory';

UPDATE meta.meta_tables SET
  table_cn = COALESCE(NULLIF(TRIM(table_cn), ''), '审批单'),
  nav_section = 'run',
  nav_order = 90,
  nav_parent_table = NULL
WHERE table_name = 'approvals';

COMMIT;
