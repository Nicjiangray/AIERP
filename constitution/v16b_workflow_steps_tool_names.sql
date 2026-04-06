-- V16b：workflow_steps.instruction / tool 与 context_schema 对齐（旧工具名 → submit）
-- 执行：psql -d aierp -f constitution/v16b_workflow_steps_tool_names.sql

BEGIN;

UPDATE business.workflow_steps
SET instruction = '用 ILIKE 模糊匹配客户名称；取得 id（作为 customer_id）与客户名称（作 customer_name）。',
    updated_at = now()
WHERE workflow_intent = 'create_contract' AND seq = 1;

UPDATE business.workflow_steps
SET instruction = REPLACE(instruction, 'write_entity(table_name=customers)', 'submit'),
    updated_at = now()
WHERE instruction LIKE '%write_entity(table_name=customers)%';

UPDATE business.workflow_steps
SET instruction = REPLACE(instruction, 'write_entity(table_name=contacts)', 'submit'),
    updated_at = now()
WHERE instruction LIKE '%write_entity(table_name=contacts)%';

UPDATE business.workflow_steps
SET instruction = REPLACE(instruction, 'write_entity(table_name=products)', 'submit'),
    updated_at = now()
WHERE instruction LIKE '%write_entity(table_name=products)%';

UPDATE business.workflow_steps
SET instruction = REPLACE(instruction, 'write_entity', 'submit'),
    updated_at = now()
WHERE instruction LIKE '%write_entity%' AND workflow_intent LIKE '%simple_entity%';

UPDATE business.workflow_steps
SET instruction = REPLACE(instruction, 'update_entity', 'submit'),
    updated_at = now()
WHERE instruction LIKE '%update_entity%';

UPDATE business.workflow_steps
SET instruction = REPLACE(instruction, 'delete_entity', 'submit'),
    updated_at = now()
WHERE instruction LIKE '%delete_entity%';

UPDATE business.workflow_steps
SET instruction = REPLACE(instruction, 'submit_contract', 'submit'),
    updated_at = now()
WHERE instruction LIKE '%submit_contract%';

UPDATE business.workflow_steps
SET tool = 'submit', updated_at = now()
WHERE tool IS NOT NULL AND btrim(tool::text) <> '' AND tool::text <> 'submit';

COMMIT;
