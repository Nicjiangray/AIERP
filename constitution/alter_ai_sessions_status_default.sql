-- ai_sessions.status：与对话持久化约定一致，默认 in_progress（完成后可为 completed 等）
-- 执行：psql -U nicjiang -d aierp -f constitution/alter_ai_sessions_status_default.sql

ALTER TABLE business.ai_sessions
    ALTER COLUMN status SET DEFAULT 'in_progress';

COMMENT ON COLUMN business.ai_sessions.status IS '会话状态：in_progress / completed / closed 等（历史库中可能有 active）';
