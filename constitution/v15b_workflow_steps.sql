-- V15-B：workflow_steps 扩展 data_key；create_contract 增加第 2 个 write；delete_contract 拆成 3 个 write
-- 前置：v15_atomic_engine.sql、v15_workflow_steps_io.sql
-- 执行：psql -d aierp -f constitution/v15b_workflow_steps.sql

BEGIN;

ALTER TABLE business.workflow_steps
  ADD COLUMN IF NOT EXISTS data_key TEXT;

COMMENT ON COLUMN business.workflow_steps.data_key IS 'submit.data 中对应本步骤的键（如 contract / items）；可空则按表名/别名解析';

-- create_contract：主表 write 在 seq 5，补充 seq 6 写明细
UPDATE business.workflow_steps
SET table_name = 'contracts', data_key = 'contract'
WHERE workflow_intent = 'create_contract' AND seq = 5;

INSERT INTO business.workflow_steps (
    workflow_intent, seq, action, label, table_name, instruction, tool, data_key
) VALUES (
    'create_contract', 6, 'write', '写入合同明细', 'contract_items',
    NULL, NULL, 'items'
)
ON CONFLICT (workflow_intent, seq) DO UPDATE SET
    action = EXCLUDED.action,
    label = EXCLUDED.label,
    table_name = EXCLUDED.table_name,
    data_key = EXCLUDED.data_key;

UPDATE business.workflow_steps
SET depends_on = ARRAY[1,2,3,4,5]
WHERE workflow_intent = 'create_contract' AND seq = 6;

-- delete_contract：seq 3 定为 contracts 软删，新增 seq 4/5
UPDATE business.workflow_steps
SET action = 'write',
    table_name = 'contracts',
    label = '软删合同主表',
    data_key = NULL
WHERE workflow_intent = 'delete_contract' AND seq = 3;

INSERT INTO business.workflow_steps (
    workflow_intent, seq, action, label, table_name, instruction, tool, data_key
) VALUES
(
    'delete_contract', 4, 'write', '软删合同明细', 'contract_items',
    NULL, NULL, NULL
),
(
    'delete_contract', 5, 'write', '写入合同变更记录', 'contract_changes',
    NULL, NULL, NULL
)
ON CONFLICT (workflow_intent, seq) DO UPDATE SET
    action = EXCLUDED.action,
    label = EXCLUDED.label,
    table_name = EXCLUDED.table_name;

UPDATE business.workflow_steps
SET depends_on = ARRAY[3]
WHERE workflow_intent = 'delete_contract' AND seq IN (4, 5);

COMMIT;
