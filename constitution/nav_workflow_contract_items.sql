-- 导航：系统准备 — workflows / workflow_steps；系统运行 — contracts / contract_items（父子）
-- 执行：psql -d aierp -f constitution/nav_workflow_contract_items.sql

UPDATE meta.meta_tables SET
  table_cn = COALESCE(NULLIF(TRIM(table_cn), ''), '工作流定义'),
  nav_section = 'prep',
  nav_order = 20,
  nav_parent_table = NULL
WHERE table_name = 'workflows';

UPDATE meta.meta_tables SET
  table_cn = COALESCE(NULLIF(TRIM(table_cn), ''), '工作流步骤'),
  nav_section = 'prep',
  nav_order = 21,
  nav_parent_table = 'workflows'
WHERE table_name = 'workflow_steps';

UPDATE meta.meta_tables SET
  nav_section = 'run',
  nav_order = COALESCE(nav_order, 10),
  nav_parent_table = NULL
WHERE table_name = 'contracts';

UPDATE meta.meta_tables SET
  table_cn = COALESCE(NULLIF(TRIM(table_cn), ''), '合同明细'),
  nav_section = 'run',
  nav_order = 11,
  nav_parent_table = 'contracts'
WHERE table_name = 'contract_items';
