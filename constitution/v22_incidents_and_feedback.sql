-- ai_incidents / ai_feedback：测试反馈与错误采集（不进领域模型）
-- 执行：psql -d aierp -f constitution/v22_incidents_and_feedback.sql

BEGIN;

CREATE TABLE IF NOT EXISTS business.ai_incidents (
    id SERIAL PRIMARY KEY,
    session_id INT REFERENCES business.ai_sessions(id),
    user_id INT REFERENCES business.employees(id),
    stage VARCHAR(30) NOT NULL,
    user_input TEXT,
    generated_sql TEXT,
    error_message TEXT NOT NULL,
    context JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);

COMMENT ON TABLE business.ai_incidents IS '系统错误自动记录：SQL 审计/执行/LLM 调用/确认等环节的异常，用于问题分析和迭代';

CREATE INDEX IF NOT EXISTS idx_incidents_stage ON business.ai_incidents(stage);
CREATE INDEX IF NOT EXISTS idx_incidents_created ON business.ai_incidents(created_at);
CREATE INDEX IF NOT EXISTS idx_incidents_session ON business.ai_incidents(session_id);

CREATE TABLE IF NOT EXISTS business.ai_feedback (
    id SERIAL PRIMARY KEY,
    session_id INT REFERENCES business.ai_sessions(id),
    user_id INT REFERENCES business.employees(id),
    chat_id INT REFERENCES business.ai_chat(id),
    feedback_type VARCHAR(20) NOT NULL DEFAULT 'thumbs_down',
    content TEXT,
    recent_messages JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);

COMMENT ON TABLE business.ai_feedback IS '用户主动反馈：点👎、对话中纠错、提建议等，用于改进 AI 回答质量';

CREATE INDEX IF NOT EXISTS idx_feedback_type ON business.ai_feedback(feedback_type);
CREATE INDEX IF NOT EXISTS idx_feedback_created ON business.ai_feedback(created_at);
CREATE INDEX IF NOT EXISTS idx_feedback_session ON business.ai_feedback(session_id);

COMMIT;

CALL meta.sync_constitution();

UPDATE meta.meta_tables
SET include_in_domain_model = false
WHERE table_schema = 'business'
  AND table_name IN ('ai_incidents', 'ai_feedback');
