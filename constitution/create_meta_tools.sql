-- meta_tools：工具声明表（collecting agent 可调用的工具定义与校验规则）
-- 见 cursor_step1_create_meta_tools.md

CREATE TABLE IF NOT EXISTS meta.meta_tools (
    tool_name       VARCHAR(64)  PRIMARY KEY,
    tool_type       VARCHAR(32)  NOT NULL,       -- read / create / update / delete / ask
    description     TEXT         NOT NULL,        -- 给 LLM 看的工具说明
    parameters_schema JSONB      NOT NULL,        -- OpenAI function calling 的 parameters 对象
    validations     JSONB        NOT NULL DEFAULT '[]'::jsonb,  -- 校验规则数组
    confirm_required BOOLEAN     NOT NULL DEFAULT false,
    enabled         BOOLEAN      NOT NULL DEFAULT true,
    sort_order      INTEGER      NOT NULL DEFAULT 0
);

COMMENT ON TABLE  meta.meta_tools IS '工具声明表：collecting agent 可调用的工具定义与校验规则';
COMMENT ON COLUMN meta.meta_tools.tool_name IS '工具名，与 LLM function calling 的 name 一致';
COMMENT ON COLUMN meta.meta_tools.tool_type IS '执行类型：read=只读查询, create=新建, update=修改, delete=删除, ask=追问用户';
COMMENT ON COLUMN meta.meta_tools.description IS '工具描述，注入 LLM 的 function.description';
COMMENT ON COLUMN meta.meta_tools.parameters_schema IS 'OpenAI function calling 格式的 parameters JSON Schema';
COMMENT ON COLUMN meta.meta_tools.validations IS '校验规则数组，每项 {"field":"...","rule":"...","params":{}}';
COMMENT ON COLUMN meta.meta_tools.confirm_required IS '执行前是否需要用户确认';
COMMENT ON COLUMN meta.meta_tools.enabled IS '是否启用（false 则不注入 LLM 工具列表）';
COMMENT ON COLUMN meta.meta_tools.sort_order IS '工具在列表中的排序';

INSERT INTO meta.meta_tools (tool_name, tool_type, description, parameters_schema, validations, confirm_required, enabled, sort_order)
VALUES (
    'query_db',
    'read',
    '查询数据库获取参考信息。只读 SELECT。适用场景：查现有编号、查枚举值、查关联记录、核实数据是否存在。只允许查 business.* 和 meta.* schema。',
    '{
        "type": "object",
        "properties": {
            "sql": {"type": "string", "description": "只读 SELECT 语句"},
            "reason": {"type": "string", "description": "为什么要查这个"}
        },
        "required": ["sql", "reason"]
    }'::jsonb,
    '[
        {"field": "sql", "rule": "required"},
        {"field": "reason", "rule": "required"},
        {"field": "sql", "rule": "must_be_select"}
    ]'::jsonb,
    false,
    true,
    1
)
ON CONFLICT (tool_name) DO UPDATE SET
    tool_type = EXCLUDED.tool_type,
    description = EXCLUDED.description,
    parameters_schema = EXCLUDED.parameters_schema,
    validations = EXCLUDED.validations,
    confirm_required = EXCLUDED.confirm_required,
    enabled = EXCLUDED.enabled,
    sort_order = EXCLUDED.sort_order;

INSERT INTO meta.meta_tools (tool_name, tool_type, description, parameters_schema, validations, confirm_required, enabled, sort_order)
VALUES (
    'write_entity',
    'create',
    '信息齐全后调用，触发人工确认流程，不直接写库。仅在已收集到 meta 中定义的、可写入的字段时使用；table_name 须为 entity_type=simple 的表白名单中的英文表名。',
    '{
        "type": "object",
        "properties": {
            "table_name": {
                "type": "string",
                "description": "目标业务表英文名（如 customers、products）"
            },
            "fields": {
                "type": "object",
                "description": "字段名（英文）到值的映射，键须为 meta.meta_fields 中存在的字段",
                "additionalProperties": true
            },
            "explanation": {
                "type": "string",
                "description": "自然语言说明本次写入的内容与依据，供用户确认时阅读"
            }
        },
        "required": ["table_name", "fields", "explanation"]
    }'::jsonb,
    '[
        {"field": "table_name", "rule": "required"},
        {"field": "table_name", "rule": "in_simple_whitelist"},
        {"field": "fields", "rule": "required"},
        {"field": "fields", "rule": "meta_field_whitelist"},
        {"field": "fields", "rule": "required_fields_check"},
        {"field": "fields", "rule": "allowed_values_check"},
        {"field": "explanation", "rule": "required"}
    ]'::jsonb,
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

INSERT INTO meta.meta_tools (tool_name, tool_type, description, parameters_schema, validations, confirm_required, enabled, sort_order)
VALUES (
    'update_entity',
    'update',
    '找到目标记录并更新指定字段。调用前必须先用 query_db 确认找到唯一记录并获取其 id。若 query_db 返回多条，必须先用 ask_user 让用户确认是哪一条。只适用于 entity_type=simple 的表。',
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
            "fields": {
                "type": "object",
                "description": "要更新的字段名→新值",
                "additionalProperties": true
            },
            "explanation": {
                "type": "string",
                "description": "自然语言说明改了什么、依据是什么"
            }
        },
        "required": ["table_name", "record_id", "fields", "explanation"]
    }'::jsonb,
    '[
        {"field": "table_name", "rule": "required"},
        {"field": "table_name", "rule": "in_simple_whitelist"},
        {"field": "record_id", "rule": "required"},
        {"field": "record_id", "rule": "verified_record_id"},
        {"field": "fields", "rule": "required"},
        {"field": "fields", "rule": "meta_field_whitelist"},
        {"field": "fields", "rule": "allowed_values_check"},
        {"field": "explanation", "rule": "required"}
    ]'::jsonb,
    true,
    true,
    3
)
ON CONFLICT (tool_name) DO UPDATE SET
    tool_type = EXCLUDED.tool_type,
    description = EXCLUDED.description,
    parameters_schema = EXCLUDED.parameters_schema,
    validations = EXCLUDED.validations,
    confirm_required = EXCLUDED.confirm_required,
    enabled = EXCLUDED.enabled,
    sort_order = EXCLUDED.sort_order;

INSERT INTO meta.meta_tools (tool_name, tool_type, description, parameters_schema, validations, confirm_required, enabled, sort_order)
VALUES (
    'ask_user',
    'ask',
    '需要向用户追问缺失、模糊或冲突信息时调用。调用后由系统将问题展示给用户，并等待其回复。',
    '{
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "要向用户提出的问题（单条、清晰）"
            },
            "hint": {
                "type": "string",
                "description": "可选：枚举值、格式示例或填写提示"
            }
        },
        "required": ["question"]
    }'::jsonb,
    '[
        {"field": "question", "rule": "required"}
    ]'::jsonb,
    false,
    true,
    4
)
ON CONFLICT (tool_name) DO UPDATE SET
    tool_type = EXCLUDED.tool_type,
    description = EXCLUDED.description,
    parameters_schema = EXCLUDED.parameters_schema,
    validations = EXCLUDED.validations,
    confirm_required = EXCLUDED.confirm_required,
    enabled = EXCLUDED.enabled,
    sort_order = EXCLUDED.sort_order;
