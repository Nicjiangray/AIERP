-- sessions：会话级 memo（JSON），供后续 Step 2+ 读写
ALTER TABLE business.sessions
    ADD COLUMN IF NOT EXISTS memo JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN business.sessions.memo IS '会话级键值 memo，默认空对象';
