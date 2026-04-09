-- 工具定义提示词：query（与 engine/query_engine 约定一致）
-- 执行：psql -d aierp -f constitution/seed_prompt_tool_query.sql

INSERT INTO meta.prompts (
    prompt_code,
    category,
    title,
    summary,
    content,
    sort_order,
    inject_mode,
    source,
    llm_editable,
    is_active,
    updated_by
) VALUES (
    'tool_query',
    'tool_definition',
    '工具：query（查询数据）',
    '结构化查询业务表的调用格式与操作符说明',
    $c$## 工具：query（查询数据）

用途：从数据库查询业务数据。你不知道任何具体数据，凡是需要引用数据库中的值，必须先调用此工具。

调用格式：
{
  "action": "query",
  "table": "表名",
  "filters": [
    {"field": "字段名", "op": "操作符", "value": "查询值"}
  ],
  "fields": ["字段1", "字段2"],
  "limit": 条数上限
}

可用操作符：
- eq：精确匹配
- neq：不等于
- contains：模糊匹配（用于姓名、名称等文本字段）
- gt / lt / gte / lte：大于、小于、大于等于、小于等于（用于日期、金额）
- in：匹配多个值，value传数组
- is_null：字段为空，无需传value

规则：
- fields不传则返回该表所有字段
- limit最大100，不传默认返回20条
- filters可以传多个，多个条件之间是AND关系$c$,
    20,
    'always',
    'manual',
    FALSE,
    TRUE,
    'system'
)
ON CONFLICT (prompt_code) DO UPDATE SET
    category = EXCLUDED.category,
    title = EXCLUDED.title,
    summary = EXCLUDED.summary,
    content = EXCLUDED.content,
    sort_order = EXCLUDED.sort_order,
    inject_mode = EXCLUDED.inject_mode,
    source = EXCLUDED.source,
    llm_editable = EXCLUDED.llm_editable,
    is_active = EXCLUDED.is_active,
    version = meta.prompts.version + 1,
    updated_at = NOW(),
    updated_by = EXCLUDED.updated_by;
