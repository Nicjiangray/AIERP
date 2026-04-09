-- 删除用户反馈表（不再使用）
-- 执行：psql -d aierp -f constitution/drop_business_feedback.sql
-- 完成后建议：CALL meta.sync_constitution();

DROP TABLE IF EXISTS business.feedback;
