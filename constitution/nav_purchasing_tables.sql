-- 前端数据导航：采购模块（suppliers + purchase_orders + purchase_order_items）
-- 依赖：meta.meta_tables 已有 nav_section / nav_order / nav_parent_table 列（见 oldfiles/constitution/add_nav_columns_meta_tables.sql 或等价迁移）
-- 执行：psql -U nicjiang -d aierp -f constitution/nav_purchasing_tables.sql

-- 系统准备：供应商主数据（与客户、产品同级的主数据区）
UPDATE meta.meta_tables SET
  table_cn = COALESCE(NULLIF(TRIM(table_cn), ''), '供应商'),
  nav_section = 'prep',
  nav_order = 40,
  nav_parent_table = NULL
WHERE table_name = 'suppliers';

-- 系统运行：采购订单（对齐 contracts / contract_items 的父子导航）
UPDATE meta.meta_tables SET
  table_cn = COALESCE(NULLIF(TRIM(table_cn), ''), '采购订单'),
  nav_section = 'run',
  nav_order = 12,
  nav_parent_table = NULL
WHERE table_name = 'purchase_orders';

UPDATE meta.meta_tables SET
  table_cn = COALESCE(NULLIF(TRIM(table_cn), ''), '采购订单明细'),
  nav_section = 'run',
  nav_order = 13,
  nav_parent_table = 'purchase_orders'
WHERE table_name = 'purchase_order_items';
