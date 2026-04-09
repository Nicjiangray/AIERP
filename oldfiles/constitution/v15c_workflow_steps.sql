-- V15-C：workflow_steps.where_field（软删按 id 或 contract_id 等）
-- 执行：psql -d aierp -f constitution/v15c_workflow_steps.sql

BEGIN;

ALTER TABLE business.workflow_steps
  ADD COLUMN IF NOT EXISTS where_field TEXT;

COMMENT ON COLUMN business.workflow_steps.where_field IS 'soft_delete 时 WHERE 列名，默认 id；contract_items 批量软删用 contract_id';

UPDATE business.workflow_steps
SET where_field = 'contract_id'
WHERE workflow_intent = 'delete_contract'
  AND action = 'write'
  AND table_name = 'contract_items';

-- 其余 write 步骤保持 NULL → 应用层视为 id
COMMIT;
