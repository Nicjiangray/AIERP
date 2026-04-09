-- meta.prompts：新增 agent_role + 记忆管理员两条提示词（on_demand）
-- 执行：psql -U nicjiang -d aierp -f constitution/add_meta_prompts_agent_role.sql

-- 任务1：新增 agent 角色字段
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'meta'
          AND table_name = 'prompts'
          AND column_name = 'agent_role'
    ) THEN
        ALTER TABLE meta.prompts
            ADD COLUMN agent_role VARCHAR(30) DEFAULT 'assistant';
    END IF;
END $$;

COMMENT ON COLUMN meta.prompts.agent_role IS 'agent角色标识：assistant=主助手，memory_manager=记忆管理员，tool_expert=工具专家，auditor=审计员';

UPDATE meta.prompts SET agent_role = 'assistant' WHERE agent_role IS NULL;

ALTER TABLE meta.prompts ALTER COLUMN agent_role SET NOT NULL;

-- 任务2：记忆管理员提示词（幂等：按 prompt_code 覆盖更新）
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
    version,
    agent_role,
    parent_code,
    updated_by
)
VALUES (
    'memory_session_clean',
    'system',
    '记忆管理员：会话清洗',
    '从对话历史中提取陈述性业务要点',
    '你是记忆管理员。你的任务是从一段对话历史中提取业务事实要点。

规则：
- 只提取已确认的事实，不提取疑问、闲聊、系统提示
- 提取涉及的实体及其ID（客户、合同、产品等）
- 提取已确认的字段值（金额、日期、数量等）
- 提取用户明确表达的意图或决定
- 每条≤20字
- 格式：A. xxx  B. xxx  C. xxx
- 没有有效事实就输出"无"

对话内容：
{chat_history}',
    10,
    'on_demand',
    'manual',
    FALSE,
    TRUE,
    1,
    'memory_manager',
    NULL,
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
    parent_code = EXCLUDED.parent_code,
    updated_at = NOW(),
    updated_by = EXCLUDED.updated_by;

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
    version,
    agent_role,
    parent_code,
    updated_by
)
VALUES (
    'memory_longterm_compress',
    'system',
    '记忆管理员：长期记忆压缩',
    '将当日要点与旧长期记忆合并压缩',
    '你是记忆管理员。你的任务是将当日业务要点与已有长期记忆合并压缩为一份新的长期记忆。

输出格式：
【活跃实体】
- 实体名(id)：当前状态一句话

【近期已完成】
- 已结束事项的结论，一句话

【业务规律】
- 从历史中观察到的稳定模式或用户习惯

规则：
- 同一实体只保留最新状态
- 已完成操作只留结论，不留过程
- 超过7天未提及的实体可删除
- 总量不超过3000字
- 如发现新的业务规律，单独列出

当日要点：
{today_sessions}

已有长期记忆：
{existing_longterm}',
    20,
    'on_demand',
    'manual',
    FALSE,
    TRUE,
    1,
    'memory_manager',
    NULL,
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
    parent_code = EXCLUDED.parent_code,
    updated_at = NOW(),
    updated_by = EXCLUDED.updated_by;
