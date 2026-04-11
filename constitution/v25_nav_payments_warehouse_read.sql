-- 侧栏 /api/user-menu：补全 warehouse 对 business.payments 的读权限
-- 原因：v17 种子中 sales/finance 含 payments，warehouse 未含，导致仓管账号导航中看不到「收付款」主表
-- 幂等：ON CONFLICT 更新
-- 执行：psql -d aierp -f constitution/v25_nav_payments_warehouse_read.sql

INSERT INTO meta.role_permissions (role_code, table_name, can_read, can_write, can_delete)
VALUES ('warehouse', 'business.payments', true, false, false)
ON CONFLICT (role_code, table_name) DO UPDATE SET
  can_read = EXCLUDED.can_read,
  can_write = EXCLUDED.can_write,
  can_delete = EXCLUDED.can_delete,
  updated_at = now();

-- 确保导航元数据存在（未跑 v23 或曾被清空时）
UPDATE meta.meta_tables SET
  table_cn = COALESCE(NULLIF(TRIM(table_cn), ''), '收付款'),
  nav_section = 'run',
  nav_order = 40,
  nav_parent_table = NULL
WHERE table_name = 'payments';
