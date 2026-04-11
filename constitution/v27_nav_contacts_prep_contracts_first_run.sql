-- 修正导航：联系人 contacts 回到「系统准备」；销售合同 contracts 在「系统运行」最前，侧栏显示名「合同管理」
-- 适用：已执行过旧版 v26（曾把 contacts 放进 run、contracts 在末尾）的库；全新安装以当前 v23+v26 为准时可跳过
-- 执行：psql -U nicjiang -d aierp -v ON_ERROR_STOP=1 -f constitution/v27_nav_contacts_prep_contracts_first_run.sql

BEGIN;

UPDATE meta.meta_tables SET
  table_cn = COALESCE(NULLIF(TRIM(table_cn), ''), '联系人'),
  nav_section = 'prep',
  nav_order = 11,
  nav_parent_table = NULL
WHERE table_name = 'contacts';

UPDATE meta.meta_tables SET
  table_cn = COALESCE(NULLIF(TRIM(table_cn), ''), '合同管理'),
  nav_section = 'run',
  nav_order = 10,
  nav_parent_table = NULL
WHERE table_name = 'contracts';

COMMIT;
