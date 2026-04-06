-- 用户反馈表：一键提交对话上下文供开发者排查
BEGIN;

CREATE TABLE IF NOT EXISTS business.feedback (
    id              SERIAL PRIMARY KEY,
    session_id      INTEGER REFERENCES business.sessions(id),
    frame_id        INTEGER REFERENCES business.frames(id),
    user_id         INTEGER DEFAULT 2,

    user_comment    TEXT NOT NULL DEFAULT '',
    feedback_type   VARCHAR(30) NOT NULL DEFAULT 'bug',

    recent_turns    JSONB NOT NULL DEFAULT '[]',
    frame_snapshot    JSONB DEFAULT '{}',
    intent            VARCHAR(100),
    error_message     TEXT,

    page_url        TEXT,
    status          VARCHAR(20) NOT NULL DEFAULT 'open',
    resolved_note   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_feedback_status ON business.feedback(status);
CREATE INDEX IF NOT EXISTS idx_feedback_session ON business.feedback(session_id);

COMMENT ON TABLE business.feedback IS '用户反馈表：自动打包对话上下文，供开发者排查问题';

INSERT INTO meta.meta_tables (table_name, table_cn, description, nav_section, entity_type)
SELECT
    'feedback',
    '用户反馈',
    '用户在 AI 对话中提交的反馈，含对话上下文和系统状态快照',
    'design',
    'log'
WHERE NOT EXISTS (
    SELECT 1 FROM meta.meta_tables WHERE table_name = 'feedback'
);

COMMIT;
