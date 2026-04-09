-- 拆分 prompt：schema/tool 归 tool_expert；新增主助手 tool_*_interface（always）
-- 执行：psql -U nicjiang -d aierp -f constitution/update_meta_prompts_tool_expert_and_interfaces.sql

-- 1. schema_shadow 归属工具专家
UPDATE meta.prompts SET agent_role = 'tool_expert' WHERE prompt_code = 'schema_shadow';

-- 2. tool_query 归属工具专家
UPDATE meta.prompts
SET agent_role = 'tool_expert', title = '工具执行：query（查询数据）'
WHERE prompt_code = 'tool_query';

-- 3. tool_write 归属工具专家
UPDATE meta.prompts
SET agent_role = 'tool_expert', title = '工具执行：write（写入数据）'
WHERE prompt_code = 'tool_write';

-- 4. 主助手：query 工具接口描述（幂等）
INSERT INTO meta.prompts (
    prompt_code, category, title, summary, content, sort_order,
    inject_mode, source, llm_editable, is_active, version, agent_role,
    updated_by
)
VALUES (
    'tool_query_interface',
    'tool_interface',
    '工具接口：query（查询数据）',
    '告诉主助手如何发出查询指令',
    '## 可用工具：查询数据

当你需要查询数据库中的信息时，输出以下JSON指令，系统会自动执行并返回结果。

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

可用操作符：eq、neq、contains、gt、lt、gte、lte、in、is_null

注意：
- 你不知道任何具体数据，必须先查询再引用
- filters可传多个，多个条件之间是AND关系
- limit最大100，默认20条',
    10,
    'always',
    'manual',
    FALSE,
    TRUE,
    1,
    'assistant',
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
    version = EXCLUDED.version,
    agent_role = EXCLUDED.agent_role,
    updated_at = NOW(),
    updated_by = EXCLUDED.updated_by;

-- 5. 主助手：write 工具接口描述（幂等）
INSERT INTO meta.prompts (
    prompt_code, category, title, summary, content, sort_order,
    inject_mode, source, llm_editable, is_active, version, agent_role,
    updated_by
)
VALUES (
    'tool_write_interface',
    'tool_interface',
    '工具接口：write（写入数据）',
    '告诉主助手如何发出写入指令',
    '## 可用工具：写入数据

当你需要新建、修改或删除记录时，输出以下JSON指令。系统会先生成操作摘要供用户确认，确认后才执行。

【新建】
{
  "action": "write",
  "operation": "create",
  "table": "表名",
  "data": { "字段名": "值" }
}

【修改】
{
  "action": "write",
  "operation": "update",
  "table": "表名",
  "id": 记录ID,
  "data": { "字段名": "新值" }
}

【删除】
{
  "action": "write",
  "operation": "delete",
  "table": "表名",
  "id": 记录ID,
  "reason": "删除原因"
}

注意：
- 所有写操作必须先向用户展示摘要并获得确认
- update和delete必须有明确的记录ID
- 禁止写入id、created_at等系统字段',
    20,
    'always',
    'manual',
    FALSE,
    TRUE,
    1,
    'assistant',
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
    version = EXCLUDED.version,
    agent_role = EXCLUDED.agent_role,
    updated_at = NOW(),
    updated_by = EXCLUDED.updated_by;
