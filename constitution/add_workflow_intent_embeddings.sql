-- 意图示例句与向量（由 scripts/sync_intent_embeddings.py 填充 query_embeddings）
ALTER TABLE business.workflows
ADD COLUMN IF NOT EXISTS example_queries JSONB DEFAULT '[]'::jsonb,
ADD COLUMN IF NOT EXISTS query_embeddings JSONB DEFAULT '[]'::jsonb;
