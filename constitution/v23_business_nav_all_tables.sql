-- 将 business 业务主表挂入侧栏；子表明细不出现在左侧栏（仅主表）
-- 系统准备含「联系人」；系统运行区顺序：合同管理 → 合同变更 → 采购 → 收付款 → 到货 → 发货 → 库存；其后审批单
-- 增量修正见 v26 / v27（已跑过 v23 的库按需执行）
-- 之后「谁可见」仍由 business.role_permissions / business.user_permissions 控制
-- 执行：psql -d aierp -f constitution/v23_business_nav_all_tables.sql

BEGIN;

-- ---------------------------------------------------------------------------
-- 子表：不参与侧栏导航（明细在各自主表界面或路由中查看）
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- 系统准备（prep）：主数据 / 配置（自上而下）
-- ---------------------------------------------------------------------------
UPDATE meta.meta_tables SET
  table_cn = COALESCE(NULLIF(TRIM(table_cn), ''), '客户'),
  nav_section = 'prep',
  nav_order = 10,
  nav_parent_table = NULL
WHERE table_name = 'customers';

UPDATE meta.meta_tables SET
  table_cn = COALESCE(NULLIF(TRIM(table_cn), ''), '联系人'),
  nav_section = 'prep',
  nav_order = 11,
  nav_parent_table = NULL
WHERE table_name = 'contacts';

UPDATE meta.meta_tables SET
  table_cn = COALESCE(NULLIF(TRIM(table_cn), ''), '产品'),
  nav_section = 'prep',
  nav_order = 12,
  nav_parent_table = NULL
WHERE table_name = 'products';

UPDATE meta.meta_tables SET
  table_cn = COALESCE(NULLIF(TRIM(table_cn), ''), '员工'),
  nav_section = 'prep',
  nav_order = 15,
  nav_parent_table = NULL
WHERE table_name = 'employees';

UPDATE meta.meta_tables SET
  table_cn = COALESCE(NULLIF(TRIM(table_cn), ''), '供应商'),
  nav_section = 'prep',
  nav_order = 20,
  nav_parent_table = NULL
WHERE table_name = 'suppliers';

UPDATE meta.meta_tables SET
  table_cn = COALESCE(NULLIF(TRIM(table_cn), ''), '合同模板'),
  nav_section = 'prep',
  nav_order = 25,
  nav_parent_table = NULL
WHERE table_name = 'contract_templates';

UPDATE meta.meta_tables SET
  table_cn = COALESCE(NULLIF(TRIM(table_cn), ''), '仪表盘卡片'),
  nav_section = 'prep',
  nav_order = 40,
  nav_parent_table = NULL
WHERE table_name = 'dashboard_cards';

UPDATE meta.meta_tables SET
  table_cn = COALESCE(NULLIF(TRIM(table_cn), ''), '个人表权限'),
  nav_section = 'prep',
  nav_order = 50,
  nav_parent_table = NULL
WHERE table_name = 'user_permissions';

UPDATE meta.meta_tables SET
  table_cn = COALESCE(NULLIF(TRIM(table_cn), ''), '账号角色授权'),
  nav_section = 'prep',
  nav_order = 51,
  nav_parent_table = NULL
WHERE table_name = 'user_role_grants';

UPDATE meta.meta_tables SET
  table_cn = COALESCE(NULLIF(TRIM(table_cn), ''), '工作流定义'),
  nav_section = 'prep',
  nav_order = 60,
  nav_parent_table = NULL
WHERE table_name = 'workflows';

-- ---------------------------------------------------------------------------
-- 系统运行（run）：业务主流程自上而下（与 v26 一致；合同管理置顶）
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- 兜底：其余 business 主表仍无导航时，挂在「系统运行」末尾（排除子表与系统表）
-- ---------------------------------------------------------------------------
WITH numbered AS (
    SELECT m.table_name,
           ROW_NUMBER() OVER (ORDER BY m.table_name) AS rn
    FROM meta.meta_tables m
    JOIN information_schema.tables t
      ON t.table_schema = 'business'
     AND t.table_name = m.table_name
     AND t.table_type = 'BASE TABLE'
    WHERE m.deleted_at IS NULL
      AND m.nav_section IS NULL
      AND m.table_name NOT IN (
          'ai_chat',
          'ai_sessions',
          'ai_memory',
          'ai_incidents',
          'ai_feedback',
          'user_accounts',
          'contract_items',
          'purchase_order_items',
          'receiving_note_items',
          'shipping_note_items',
          'payment_allocations',
          'workflow_steps'
      )
      AND (LENGTH(m.table_name) < 6 OR RIGHT(m.table_name, 6) <> '_items')
)
UPDATE meta.meta_tables AS m
SET
  nav_section = 'run',
  nav_order = 700 + n.rn,
  nav_parent_table = NULL
FROM numbered AS n
WHERE m.table_name = n.table_name;

COMMIT;

-- ---------------------------------------------------------------------------
-- admin：对全部 business 表保留完整 role_permissions（子表仍可有权限，便于 SQL/界面）
-- ---------------------------------------------------------------------------
INSERT INTO business.role_permissions (role_code, table_name, can_read, can_write, can_delete)
SELECT
    'admin',
    'business.' || tablename,
    true,
    true,
    true
FROM pg_tables
WHERE schemaname = 'business'
  AND tablename NOT IN ('ai_chat', 'ai_sessions', 'ai_memory', 'user_accounts')
ON CONFLICT (role_code, table_name) DO UPDATE SET
  can_read = EXCLUDED.can_read,
  can_write = EXCLUDED.can_write,
  can_delete = EXCLUDED.can_delete,
  updated_at = now();
