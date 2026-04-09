-- V15：三原子工具（query_db / submit / ask_user）+ workflow_steps 扩展列 + context_schema.allowed_tools
-- 执行：psql -d aierp -f constitution/v15_atomic_engine.sql
-- 说明：旧工具行保留可由应用兼容；本脚本删除旧工具并插入 submit。
BEGIN;

-- 1) workflow_steps：扩展列（V15 设计）
ALTER TABLE business.workflow_steps
  ADD COLUMN IF NOT EXISTS input_fields JSONB DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS output_fields JSONB DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS depends_on INTEGER[];

COMMENT ON COLUMN business.workflow_steps.input_fields IS '步骤所需输入（查询条件/用户字段）；V15 设计';
COMMENT ON COLUMN business.workflow_steps.output_fields IS '步骤产出字段；V15 设计';
COMMENT ON COLUMN business.workflow_steps.depends_on IS '依赖的同意图前序步骤 seq 数组；V15 设计';

-- 2) 统一 action：lookup→query，submit→write（与 V15 文档一致；若已迁移可重复执行）
UPDATE business.workflow_steps SET action = 'query' WHERE action = 'lookup';
UPDATE business.workflow_steps SET action = 'write' WHERE action = 'submit';

-- 3) meta_tools：仅保留三原子（删除旧 CUD/合同专用工具名）
DELETE FROM meta.meta_tools
WHERE tool_name IN (
  'write_entity', 'update_entity', 'delete_entity',
  'submit_contract', 'update_contract'
);

INSERT INTO meta.meta_tools (
  tool_name, tool_type, description, parameters_schema, validations,
  confirm_required, enabled, sort_order
) VALUES (
  'submit',
  'write',
  '提交写操作预览（新建/更新/删除）。引擎根据 frame.workflow_intent 与 workflow_steps 编排；用户确认后才写库。',
  '{
    "type": "object",
    "properties": {
      "data": {
        "type": "object",
        "description": "业务数据，键依当前意图而定"
      },
      "reason": {
        "type": "string",
        "description": "操作原因（更新/删除时建议填写）"
      }
    },
    "required": ["data"]
  }'::jsonb,
  '[]'::jsonb,
  true,
  true,
  2
)
ON CONFLICT (tool_name) DO UPDATE SET
  tool_type = EXCLUDED.tool_type,
  description = EXCLUDED.description,
  parameters_schema = EXCLUDED.parameters_schema,
  validations = EXCLUDED.validations,
  confirm_required = EXCLUDED.confirm_required,
  enabled = EXCLUDED.enabled,
  sort_order = EXCLUDED.sort_order;

-- 4) workflows：allowed_tools 改为三原子（context_schema 仅保留 allowed_tools/prompt_rules，步骤以 workflow_steps 表为准）
UPDATE business.workflows SET context_schema =
  jsonb_set(
    COALESCE(context_schema, '{}'::jsonb),
    '{allowed_tools}',
    '["query_db", "submit", "ask_user"]'::jsonb,
    true
  )
WHERE intent IN (
  'create_simple_entity', 'update_simple_entity', 'delete_simple_entity',
  'query_master_data', 'create_contract', 'update_contract', 'delete_contract'
);

COMMIT;
