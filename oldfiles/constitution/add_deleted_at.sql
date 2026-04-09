-- delete_entity 第一步：业务表 / 元数据表 soft delete 列 + meta_tools / workflows 声明
-- 执行：psql -d aierp -f constitution/add_deleted_at.sql
-- 完成后：python scripts/sync_intent_embeddings.py

-- =============================================================================
-- 1) deleted_at 列（NULL=有效，非 NULL=已软删）
-- =============================================================================

ALTER TABLE business.customers ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP NULL;
COMMENT ON COLUMN business.customers.deleted_at IS '软删除时间戳，NULL 表示有效记录';

ALTER TABLE business.contacts ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP NULL;
COMMENT ON COLUMN business.contacts.deleted_at IS '软删除时间戳，NULL 表示有效记录';

ALTER TABLE business.products ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP NULL;
COMMENT ON COLUMN business.products.deleted_at IS '软删除时间戳，NULL 表示有效记录';

ALTER TABLE business.contract_templates ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP NULL;
COMMENT ON COLUMN business.contract_templates.deleted_at IS '软删除时间戳，NULL 表示有效记录';

ALTER TABLE business.contracts ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP NULL;
COMMENT ON COLUMN business.contracts.deleted_at IS '软删除时间戳，NULL 表示有效记录';

ALTER TABLE business.contract_items ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP NULL;
COMMENT ON COLUMN business.contract_items.deleted_at IS '软删除时间戳，NULL 表示有效记录';

ALTER TABLE business.workflows ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP NULL;
COMMENT ON COLUMN business.workflows.deleted_at IS '软删除时间戳，NULL 表示有效记录';

ALTER TABLE business.workflow_steps ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP NULL;
COMMENT ON COLUMN business.workflow_steps.deleted_at IS '软删除时间戳，NULL 表示有效记录';

ALTER TABLE business.sessions ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP NULL;
COMMENT ON COLUMN business.sessions.deleted_at IS '软删除时间戳，NULL 表示有效记录';

ALTER TABLE business.frames ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP NULL;
COMMENT ON COLUMN business.frames.deleted_at IS '软删除时间戳，NULL 表示有效记录';

ALTER TABLE business.slots ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP NULL;
COMMENT ON COLUMN business.slots.deleted_at IS '软删除时间戳，NULL 表示有效记录';

ALTER TABLE business.turns ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP NULL;
COMMENT ON COLUMN business.turns.deleted_at IS '软删除时间戳，NULL 表示有效记录';

ALTER TABLE meta.meta_tables ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP NULL;
COMMENT ON COLUMN meta.meta_tables.deleted_at IS '软删除时间戳，NULL 表示有效记录';

ALTER TABLE meta.meta_fields ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP NULL;
COMMENT ON COLUMN meta.meta_fields.deleted_at IS '软删除时间戳，NULL 表示有效记录';

ALTER TABLE meta.meta_tools ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP NULL;
COMMENT ON COLUMN meta.meta_tools.deleted_at IS '软删除时间戳，NULL 表示有效记录';

-- =============================================================================
-- 2) simple 实体：meta_fields 注册 deleted_at（系统字段，不追问、无枚举）
-- =============================================================================

INSERT INTO meta.meta_fields (table_name, field_name, field_cn, ask_if_missing, allowed_values)
VALUES
    ('customers', 'deleted_at', '删除时间', false, NULL),
    ('products', 'deleted_at', '删除时间', false, NULL),
    ('contacts', 'deleted_at', '删除时间', false, NULL)
ON CONFLICT (table_name, field_name) DO UPDATE SET
    field_cn = EXCLUDED.field_cn,
    ask_if_missing = EXCLUDED.ask_if_missing,
    allowed_values = EXCLUDED.allowed_values;

-- =============================================================================
-- 3) meta_tools：delete_entity
-- =============================================================================

INSERT INTO meta.meta_tools (tool_name, tool_type, description, parameters_schema, validations, confirm_required, enabled, sort_order)
VALUES (
    'delete_entity',
    'delete',
    '删除指定记录（软删除，标记 deleted_at）。调用前必须先用 query_db 确认找到唯一记录并获取其 id。若 query_db 返回多条，必须先用 ask_user 让用户确认是哪一条。只适用于 entity_type=simple 的表。',
    '{
        "type": "object",
        "properties": {
            "table_name": {
                "type": "string",
                "description": "目标表英文名"
            },
            "record_id": {
                "type": "integer",
                "description": "目标记录的 id"
            },
            "explanation": {
                "type": "string",
                "description": "自然语言说明要删除什么、依据是什么"
            }
        },
        "required": ["table_name", "record_id", "explanation"]
    }'::jsonb,
    '[
        {"field": "table_name", "rule": "required"},
        {"field": "table_name", "rule": "in_simple_whitelist"},
        {"field": "record_id", "rule": "required"},
        {"field": "record_id", "rule": "verified_record_id"},
        {"field": "explanation", "rule": "required"}
    ]'::jsonb,
    true,
    true,
    5
)
ON CONFLICT (tool_name) DO UPDATE SET
    tool_type = EXCLUDED.tool_type,
    description = EXCLUDED.description,
    parameters_schema = EXCLUDED.parameters_schema,
    validations = EXCLUDED.validations,
    confirm_required = EXCLUDED.confirm_required,
    enabled = EXCLUDED.enabled,
    sort_order = EXCLUDED.sort_order;

-- =============================================================================
-- 4) workflows：delete_simple_entity（列名为 intent，非 intent_key）
-- =============================================================================

INSERT INTO business.workflows (intent, display_name, description, example_queries, is_active, operation_type, target_tables)
VALUES (
    'delete_simple_entity',
    '删除简单实体',
    '删除客户、产品、联系人等简单实体记录（软删除）',
    '[
        "删除客户",
        "删掉这个客户",
        "把华东纺织删了",
        "移除这个产品",
        "删除联系人张三",
        "帮我删掉这条记录",
        "这个客户不要了",
        "去掉这个产品",
        "移除供应商",
        "删除这个联系人",
        "把这条数据删了",
        "这个客户作废",
        "产品下架删除",
        "废弃这个联系人",
        "清除这条客户记录"
    ]'::jsonb,
    1,
    'delete',
    ARRAY['customers', 'products', 'contacts']::text[]
)
ON CONFLICT (intent) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    description = EXCLUDED.description,
    example_queries = EXCLUDED.example_queries,
    is_active = EXCLUDED.is_active,
    operation_type = EXCLUDED.operation_type,
    target_tables = EXCLUDED.target_tables;
