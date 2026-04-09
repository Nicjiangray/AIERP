-- 用户 AI 记忆：会话级短期要点 + 长期压缩记忆
-- 执行：psql -U nicjiang -d aierp -f constitution/create_business_ai_memory.sql
-- 完成后建议：CALL meta.sync_constitution();

CREATE TABLE IF NOT EXISTS business.ai_memory (
    id SERIAL PRIMARY KEY,                          -- 主键
    user_id INTEGER NOT NULL,                       -- 用户ID
    scope VARCHAR(20) NOT NULL                    -- 记忆范围：session=单次会话要点，longterm=长期压缩记忆
        CHECK (scope IN ('session', 'longterm')),
    source_session_id INTEGER                       -- 来源会话ID，session级有值，longterm为null
        REFERENCES business.ai_sessions (id) ON DELETE CASCADE,
    content TEXT NOT NULL,                          -- 记忆内容（清洗/压缩后的文本）
    created_at TIMESTAMPTZ DEFAULT NOW(),           -- 创建时间
    updated_at TIMESTAMPTZ DEFAULT NOW()            -- 更新时间
);

COMMENT ON TABLE business.ai_memory IS '用户AI记忆表，存储清洗后的会话要点和长期压缩记忆';
COMMENT ON COLUMN business.ai_memory.id IS '主键';
COMMENT ON COLUMN business.ai_memory.user_id IS '关联用户ID';
COMMENT ON COLUMN business.ai_memory.scope IS '记忆范围：session=单次会话清洗要点，longterm=每日凌晨压缩的长期记忆';
COMMENT ON COLUMN business.ai_memory.source_session_id IS '来源会话ID，仅session级记忆有值';
COMMENT ON COLUMN business.ai_memory.content IS '记忆正文，清洗或压缩后的结构化文本';
COMMENT ON COLUMN business.ai_memory.created_at IS '创建时间';
COMMENT ON COLUMN business.ai_memory.updated_at IS '更新时间';

-- 索引：按用户+范围查询（注入prompt时用）
CREATE INDEX IF NOT EXISTS idx_ai_memory_user_scope ON business.ai_memory (user_id, scope);
-- 索引：按用户+时间查询（取当日session记忆用）
CREATE INDEX IF NOT EXISTS idx_ai_memory_user_created ON business.ai_memory (user_id, created_at);
