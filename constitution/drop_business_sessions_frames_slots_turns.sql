-- 删除会话/帧相关四表（business.sessions / frames / slots / turns）
-- 依赖：business.feedback 曾引用 sessions、frames，先删外键与列再删表。
-- 执行：psql -d aierp -f constitution/drop_business_sessions_frames_slots_turns.sql
-- 完成后建议：CALL meta.sync_constitution();

ALTER TABLE business.feedback DROP CONSTRAINT IF EXISTS feedback_session_id_fkey;
ALTER TABLE business.feedback DROP CONSTRAINT IF EXISTS feedback_frame_id_fkey;
DROP INDEX IF EXISTS business.idx_feedback_session;
ALTER TABLE business.feedback DROP COLUMN IF EXISTS session_id;
ALTER TABLE business.feedback DROP COLUMN IF EXISTS frame_id;

DROP TABLE IF EXISTS business.slots;
DROP TABLE IF EXISTS business.turns;
DROP TABLE IF EXISTS business.frames;
DROP TABLE IF EXISTS business.sessions;
