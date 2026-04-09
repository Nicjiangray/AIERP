-- 工具定义提示词：write（与 engine/write_engine 约定一致）
-- 执行：psql -d aierp -f constitution/seed_prompt_tool_write.sql

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
    'tool_write',
    'tool_definition',
    '工具：write（写入数据）',
    '结构化新建、更新、软删除的调用格式与约束',
    $c$## 工具：write（写入数据）

用途：对数据库执行新建、修改、软删除操作。所有write操作执行前，必须先向用户展示操作摘要并获得确认。

调用格式：

【新建记录】
{
  "action": "write",
  "operation": "create",
  "table": "表名",
  "data": { "字段名": "值" }
}

【修改记录】
{
  "action": "write",
  "operation": "update",
  "table": "表名",
  "id": 记录ID,
  "data": { "字段名": "新值" }
}

【删除记录】
{
  "action": "write",
  "operation": "delete",
  "table": "表名",
  "id": 记录ID,
  "reason": "删除原因"
}

规则：
- update和delete必须传id，禁止批量操作整张表
- delete是软删除，数据不会真实消失
- delete的reason必填，必须是用户提供的真实原因
- 禁止写入id、created_at、updated_at、deleted_at等系统字段
- 执行任何write操作前，必须先调用notify工具向用户确认$c$,
    30,
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
