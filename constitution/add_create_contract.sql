-- create_contract 第一步：submit_contract 工具 + create_contract 意图
-- 执行：psql -d aierp -f constitution/add_create_contract.sql
-- 完成后：python scripts/sync_intent_embeddings.py

-- =============================================================================
-- 1) meta_tools：submit_contract（主表 + 明细一次性提交，确认后事务写入）
-- =============================================================================

INSERT INTO meta.meta_tools (tool_name, tool_type, description, parameters_schema, validations, confirm_required, enabled, sort_order)
VALUES (
    'submit_contract',
    'create_composite',
    '提交完整销售合同数据（主表 business.contracts + 明细 business.contract_items）。信息收集完毕后一次性调用本工具，触发用户确认流程；确认后由系统在单事务中写入主表与子表。合同编号 contract_no、主表 total_amount、子表 amount 等由系统根据规则自动生成或汇总，调用时勿填。调用前应先用 query_db 核实客户存在并取得 customer_id，必要时查询 products 以填写 product_id / product_code；子表明细行须包含产品名、数量、单价等。',
    '{
        "type": "object",
        "properties": {
            "contract": {
                "type": "object",
                "description": "合同主表字段；contract_no、total_amount、status、created_by、created_at、updated_at 由系统处理，勿传",
                "properties": {
                    "customer_id": {
                        "type": "integer",
                        "description": "客户 ID，必填；须与 query_db 查询结果一致"
                    },
                    "customer_name": {
                        "type": "string",
                        "description": "客户名称，必填；与 customer_id 对应，来自查询结果"
                    },
                    "currency": {
                        "type": "string",
                        "description": "币种，可选，默认 CNY"
                    },
                    "payment_terms": {
                        "type": "string",
                        "description": "付款条件，可选"
                    },
                    "delivery_date": {
                        "type": "string",
                        "description": "交货日期，可选，建议 YYYY-MM-DD"
                    },
                    "signed_date": {
                        "type": "string",
                        "description": "签订日期，可选，建议 YYYY-MM-DD"
                    },
                    "notes": {
                        "type": "string",
                        "description": "备注，可选"
                    }
                },
                "required": ["customer_id", "customer_name"]
            },
            "items": {
                "type": "array",
                "description": "合同明细行；contract_id、amount 由系统处理，勿传；amount 由 quantity × unit_price 计算",
                "items": {
                    "type": "object",
                    "properties": {
                        "product_name": {"type": "string", "description": "产品名称，必填"},
                        "product_code": {"type": "string", "description": "产品编码，可选；可通过 query_db 从产品表查询"},
                        "product_id": {"type": "integer", "description": "产品 ID，可选"},
                        "quantity": {"type": "number", "description": "数量，必填，须大于 0"},
                        "unit_price": {"type": "number", "description": "单价，必填，须大于等于 0"},
                        "unit": {"type": "string", "description": "单位，可选"},
                        "color": {"type": "string", "description": "颜色，可选"},
                        "spec": {"type": "string", "description": "规格，可选"},
                        "notes": {"type": "string", "description": "行备注，可选"}
                    },
                    "required": ["product_name", "quantity", "unit_price"]
                }
            }
        },
        "required": ["contract", "items"]
    }'::jsonb,
    '[
        {"field": "contract.customer_id", "rule": "required"},
        {"field": "contract.customer_name", "rule": "required"},
        {"field": "items", "rule": "required"},
        {"field": "items", "rule": "must_be_nonempty_array"},
        {"field": "items[].product_name", "rule": "required"},
        {"field": "items[].quantity", "rule": "required"},
        {"field": "items[].quantity", "rule": "gt", "params": {"value": 0}},
        {"field": "items[].unit_price", "rule": "required"},
        {"field": "items[].unit_price", "rule": "gte", "params": {"value": 0}}
    ]'::jsonb,
    true,
    true,
    6
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
-- 2) workflows：create_contract
-- =============================================================================

INSERT INTO business.workflows (intent, display_name, description, example_queries, is_active, operation_type, target_tables)
VALUES (
    'create_contract',
    '创建合同',
    '创建销售合同，包含合同主信息和产品明细',
    '[
        "新建一个合同",
        "给上海纺织品公司做一份合同",
        "创建合同，客户是广州服装辅料",
        "帮我起草一份销售合同",
        "做一个合同，三个产品",
        "录入一份销售合同",
        "我要新建销售合同",
        "给客户开一份合同",
        "做一份面料合同",
        "创建外销合同",
        "帮我做一份含多条明细的合同",
        "签订销售合同",
        "出一份合同给客户",
        "新建合同订单",
        "起草合同并加上产品行"
    ]'::jsonb,
    1,
    'create',
    ARRAY['contracts', 'contract_items']::text[]
)
ON CONFLICT (intent) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    description = EXCLUDED.description,
    example_queries = EXCLUDED.example_queries,
    is_active = EXCLUDED.is_active,
    operation_type = EXCLUDED.operation_type,
    target_tables = EXCLUDED.target_tables;
