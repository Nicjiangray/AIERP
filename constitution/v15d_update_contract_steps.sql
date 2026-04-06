-- V15-D：update_contract 拆为多 write 步（submit.data 按 data_key 组织）
-- 前置：business.workflow_steps 已有 data_key / depends_on / uq_workflow_steps_intent_seq
-- 执行：psql -d aierp -f constitution/v15d_update_contract_steps.sql

BEGIN;

DELETE FROM business.workflow_steps
WHERE workflow_intent = 'update_contract' AND action = 'write';

INSERT INTO business.workflow_steps (
    workflow_intent, seq, action, label, table_name, instruction, tool, data_key, depends_on
) VALUES
(
    'update_contract', 3, 'write', '更新合同主表', 'contracts',
    '更新合同主表字段（如客户、币种、备注等）', NULL, 'header_updates',
    ARRAY[1]::integer[]
),
(
    'update_contract', 4, 'write', '新增合同明细', 'contract_items',
    '新增的合同明细行', NULL, 'items_add',
    ARRAY[1]::integer[]
),
(
    'update_contract', 5, 'write', '修改合同明细', 'contract_items',
    '修改已有的合同明细行', NULL, 'items_update',
    ARRAY[1]::integer[]
),
(
    'update_contract', 6, 'write', '删除合同明细', 'contract_items',
    '软删指定的合同明细行', NULL, 'items_remove',
    ARRAY[1]::integer[]
),
(
    'update_contract', 7, 'write', '写入变更记录', 'contract_changes',
    '写入合同变更证据链（snapshot_before/after + reason）', NULL, NULL,
    ARRAY[1,2]::integer[]
)
ON CONFLICT (workflow_intent, seq) DO UPDATE SET
    action = EXCLUDED.action,
    label = EXCLUDED.label,
    table_name = EXCLUDED.table_name,
    instruction = EXCLUDED.instruction,
    tool = EXCLUDED.tool,
    data_key = EXCLUDED.data_key,
    depends_on = EXCLUDED.depends_on;

COMMIT;
