-- 删除已废弃的工作流表：workflow_steps_legacy、workflow_steps、workflows
-- 执行：psql -U nicjiang -d aierp -f constitution/drop_workflows_tables.sql

BEGIN;

DROP TABLE IF EXISTS business.workflow_steps_legacy CASCADE;
DROP TABLE IF EXISTS business.workflow_steps CASCADE;
DROP TABLE IF EXISTS business.workflows CASCADE;

COMMIT;

CALL meta.sync_constitution();
