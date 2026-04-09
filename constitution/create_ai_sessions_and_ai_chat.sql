-- AI 对话记忆：会话主表 + 原始消息表
-- 执行：psql -d aierp -f constitution/create_ai_sessions_and_ai_chat.sql
-- 完成后建议：CALL meta.sync_constitution();

CREATE TABLE IF NOT EXISTS business.ai_sessions (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES business.employees (id),
    session_date    DATE NOT NULL DEFAULT CURRENT_DATE,
    status          VARCHAR(20) NOT NULL DEFAULT 'in_progress',
    memo_draft      JSONB,
    memo_confirmed  JSONB,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at       TIMESTAMPTZ,
    notes           TEXT
);

COMMENT ON TABLE business.ai_sessions IS 'AI对话会话主表，每次用户打开对话窗口创建一条记录';
COMMENT ON COLUMN business.ai_sessions.id IS '会话ID';
COMMENT ON COLUMN business.ai_sessions.user_id IS '操作人ID，关联 business.employees.id';
COMMENT ON COLUMN business.ai_sessions.session_date IS '会话日期，用于按天聚合memo';
COMMENT ON COLUMN business.ai_sessions.status IS '会话状态：in_progress / completed / closed 等';
COMMENT ON COLUMN business.ai_sessions.memo_draft IS '动态摘要草稿，每轮对话后由LLM覆盖更新，用户可见可改';
COMMENT ON COLUMN business.ai_sessions.memo_confirmed IS '用户确认后的最终摘要，用于注入下次会话的system prompt';
COMMENT ON COLUMN business.ai_sessions.started_at IS '会话开始时间';
COMMENT ON COLUMN business.ai_sessions.closed_at IS '会话结束时间';
COMMENT ON COLUMN business.ai_sessions.notes IS '备注';

CREATE INDEX IF NOT EXISTS idx_ai_sessions_user_session_date
    ON business.ai_sessions (user_id, session_date);

CREATE TABLE IF NOT EXISTS business.ai_chat (
    id              SERIAL PRIMARY KEY,
    session_id      INTEGER NOT NULL REFERENCES business.ai_sessions (id) ON DELETE CASCADE,
    role            VARCHAR(20) NOT NULL,
    content         TEXT NOT NULL,
    token_count     INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE business.ai_chat IS 'AI对话原始记录表，永久保留，用于上下文传递、审计和追溯';
COMMENT ON COLUMN business.ai_chat.id IS '消息ID';
COMMENT ON COLUMN business.ai_chat.session_id IS '所属会话';
COMMENT ON COLUMN business.ai_chat.role IS '发言角色：user / assistant / system';
COMMENT ON COLUMN business.ai_chat.content IS '消息原始内容';
COMMENT ON COLUMN business.ai_chat.token_count IS '本条消息的token数，可选，用于成本追踪';
COMMENT ON COLUMN business.ai_chat.created_at IS '消息时间';

CREATE INDEX IF NOT EXISTS idx_ai_chat_session_id ON business.ai_chat (session_id);
