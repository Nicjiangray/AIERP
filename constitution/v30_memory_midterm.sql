-- 中期记忆 midterm：扩展 scope CHECK；插入 memory_midterm_merge；长记忆压缩模板改为引用中期记忆
-- 执行：psql -U nicjiang -d aierp -f constitution/v30_memory_midterm.sql

ALTER TABLE business.ai_memory DROP CONSTRAINT IF EXISTS ai_memory_scope_check;
ALTER TABLE business.ai_memory
    ADD CONSTRAINT ai_memory_scope_check
    CHECK (scope IN ('session', 'longterm', 'core', 'midterm'));

COMMENT ON COLUMN business.ai_memory.scope IS
    'session=单次会话要点；midterm=合并去矛盾后的中期记忆(每用户通常一行)；longterm=长期压缩；core=从 longterm 提炼的核心经验';

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
    'memory_midterm_merge',
    'memory',
    '记忆管理员：中期记忆合并',
    '将新会话要点合并进已有中期记忆，去矛盾去重',
    $prompt$
你是记忆管理员。将「新的会话要点」合并进「已有中期记忆」，并输出更新后的完整中期记忆。

规则：
1. 同一实体或同一主题出现冲突时，只保留**最新**的表述（以新会话要点中的信息为准，若新要点未覆盖则保留旧表述）。
2. 完全重复或语义重复的事实只保留一条。
3. 新事实中不与旧记忆冲突的内容直接追加。
4. **不要压缩、不要缩写**，保留完整表述，便于后续业务使用。
5. 输出为更新后的**完整**中期记忆列表，每条一行，行首格式必须为 `A. `（字母 A、英文句点、空格）开头，例如：`A. 客户张三的信用额度已调整为 15 万`。

已有中期记忆：
{existing_midterm}

新的会话要点：
{new_session_points}
$prompt$,
    24,
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

-- 长记忆压缩：以中期记忆为原料（兼容旧占位符 {today_sessions}）
UPDATE meta.prompts
SET
    summary = '将中期记忆与旧长期记忆合并压缩',
    content = REPLACE(
        REPLACE(
            REPLACE(
                content,
                '你是记忆管理员。你的任务是将当日业务要点与已有长期记忆合并压缩为一份新的长期记忆。',
                '你是记忆管理员。你的任务是将中期记忆（已合并去矛盾）与已有长期记忆合并压缩为一份新的长期记忆。'
            ),
            '当日要点：',
            '中期记忆：'
        ),
        '{today_sessions}',
        '{midterm_memories}'
    ),
    updated_at = NOW(),
    updated_by = 'system'
WHERE prompt_code = 'memory_longterm_compress'
  AND content LIKE '%{today_sessions}%';
