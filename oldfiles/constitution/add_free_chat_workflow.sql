-- free_chat 意图：自由对话（默认未识别意图时由 main 分发到 run_collecting）
-- 执行：psql -d aierp -f constitution/add_free_chat_workflow.sql

BEGIN;

INSERT INTO business.workflows (
    intent,
    display_name,
    description,
    operation_type,
    target_tables,
    example_queries,
    context_schema,
    is_active
)
SELECT
    'free_chat',
    '自由对话',
    '用户未明确写操作意图时的默认模式。提供查询、数据分析、建议，并将业务要点写入 session memo。',
    'chat',
    ARRAY['sessions']::text[],
    '["今天有什么事要处理", "帮我看看浑圆纺织的情况", "最近合同有哪些", "分析一下上个月的销售"]'::jsonb,
    jsonb_set(
        jsonb_set(
            w.context_schema,
            '{allowed_tools}',
            '["query_db", "ask_user", "submit"]'::jsonb
        ),
        '{prompt_rules}',
        '[
            "本模式为自由对话：不强制每轮必须调用工具；闲聊或一般说明可直接文字回复。",
            "涉及业务数据时优先用 query_db 做只读 SELECT（business / meta）；需要澄清时用 ask_user。",
            "当需要把整理好的要点按 workflow 持久化时，可使用 submit（由引擎按 workflow_steps 编排）；不要编造已写入。",
            "查询业务表时 WHERE 宜包含 deleted_at IS NULL（除非用户要求查看已删除记录）；系统也可能自动过滤已删行。",
            "涉及客户、产品、合同等业务要点时，可在回复末尾按系统约定输出 <memo> 块（entities / facts / hints）。"
        ]'::jsonb
    ),
    1
FROM business.workflows w
WHERE w.intent = 'query_master_data'
ON CONFLICT (intent) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    description = EXCLUDED.description,
    operation_type = EXCLUDED.operation_type,
    target_tables = EXCLUDED.target_tables,
    example_queries = EXCLUDED.example_queries,
    context_schema = EXCLUDED.context_schema,
    is_active = EXCLUDED.is_active;

DELETE FROM business.workflow_steps WHERE workflow_intent = 'free_chat';

INSERT INTO business.workflow_steps (
    workflow_intent,
    seq,
    action,
    label,
    table_name,
    instruction,
    tool,
    fields,
    data_key
)
VALUES (
    'free_chat',
    1,
    'query',
    '业务查询',
    NULL,
    '可查询 customers、contacts、products、contracts、contract_items、contract_templates、contract_changes 等业务表。用 query_db 执行只读 SELECT。',
    NULL,
    COALESCE(
        (SELECT fields FROM business.workflow_steps WHERE workflow_intent = 'query_master_data' AND seq = 1),
        '[]'::jsonb
    )
    || COALESCE(
        (SELECT fields FROM business.workflow_steps WHERE workflow_intent = 'query_master_data' AND seq = 2),
        '[]'::jsonb
    )
    || COALESCE(
        (SELECT fields FROM business.workflow_steps WHERE workflow_intent = 'query_master_data' AND seq = 3),
        '[]'::jsonb
    )
    || COALESCE(
        (SELECT fields FROM business.workflow_steps WHERE workflow_intent = 'query_master_data' AND seq = 4),
        '[]'::jsonb
    ),
    NULL
),
(
    'free_chat',
    2,
    'collect',
    '整理要点',
    'sessions',
    '从对话和查询结果中提取业务要点，整理为 entities（涉及的客户/产品/合同等）、facts（确认的数据事实）、hints（用户可能的下一步意图）三个维度。',
    NULL,
    '[]'::jsonb,
    NULL
),
(
    'free_chat',
    3,
    'write',
    '更新备忘',
    'sessions',
    '将整理好的要点写入当前 session 的 memo 字段。',
    'submit',
    '[{"field": "memo", "field_cn": "会话备忘", "type": "jsonb"}]'::jsonb,
    'memo'
);

COMMIT;
