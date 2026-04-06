-- update_contract 工具 + 意图
-- 执行：psql -d aierp -f constitution/add_update_contract.sql
-- 完成后：python scripts/sync_intent_embeddings.py

INSERT INTO meta.meta_tools (tool_name, tool_type, description, parameters_schema, validations, confirm_required, enabled, sort_order)
VALUES (
    'update_contract',
    'update_composite',
    '修改已有合同的主表字段和/或明细行（增删改产品行）。必须先通过 query_db 确认合同 id，且须填写变更原因 reason。不要直接 UPDATE 数据库。',
    '{
        "type": "object",
        "properties": {
            "contract_id": {
                "type": "integer",
                "description": "合同 ID（须与先前 query_db 单行结果一致）"
            },
            "reason": {
                "type": "string",
                "description": "变更原因，必填"
            },
            "header_updates": {
                "type": "object",
                "description": "主表要改的字段：字段名 -> 新值；无主表变更可省略",
                "additionalProperties": true
            },
            "items_add": {
                "type": "array",
                "description": "新增明细行",
                "items": {"type": "object", "additionalProperties": true}
            },
            "items_update": {
                "type": "array",
                "description": "修改明细：每项须含 item_id（contract_items.id）及要改的字段",
                "items": {"type": "object", "additionalProperties": true}
            },
            "items_remove": {
                "type": "array",
                "description": "要软删的明细行 id 列表",
                "items": {"type": "integer"}
            }
        },
        "required": ["contract_id", "reason"]
    }'::jsonb,
    '[]'::jsonb,
    true,
    true,
    60
)
ON CONFLICT (tool_name) DO UPDATE SET
    tool_type = EXCLUDED.tool_type,
    description = EXCLUDED.description,
    parameters_schema = EXCLUDED.parameters_schema,
    validations = EXCLUDED.validations,
    confirm_required = EXCLUDED.confirm_required,
    enabled = EXCLUDED.enabled,
    sort_order = EXCLUDED.sort_order;

INSERT INTO business.workflows (intent, display_name, description, example_queries)
VALUES (
    'update_contract',
    '修改合同',
    '修改已有合同主表信息或明细（数量、单价、增减行等），须说明变更原因',
    '[
        "修改合同",
        "改一下合同",
        "合同要改",
        "把合同数量改一下",
        "追加合同明细",
        "合同里加一个产品",
        "减少合同数量",
        "删掉合同里的某一项",
        "合同单价要调整",
        "客户要求改合同",
        "更新合同内容",
        "调整合同明细",
        "合同备注改一下",
        "修改合同的签约日期",
        "合同状态改为已签署"
    ]'::jsonb
)
ON CONFLICT (intent) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    description = EXCLUDED.description,
    example_queries = EXCLUDED.example_queries;
