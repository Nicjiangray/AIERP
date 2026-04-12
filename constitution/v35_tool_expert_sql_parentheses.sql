-- ============================================================
-- V35：tool_expert_executor — WHERE 混用 AND/OR 时须加括号
-- ============================================================

UPDATE meta.prompts
SET content = content || '

【SQL 规范：WHERE 条件括号】
WHERE 子句中混用 AND 和 OR 时，必须用括号明确优先级。
错误：WHERE name = ''王芳'' AND status IS NULL OR status != ''deleted''
正确：WHERE name = ''王芳'' AND (status IS NULL OR status != ''deleted'')
',
    updated_at = NOW()
WHERE prompt_code = 'tool_expert_executor'
  AND agent_role = 'tool_expert';
