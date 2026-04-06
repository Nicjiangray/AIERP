-- ============================================================
-- business.design_memories — 设计/架构过程记忆（与 business.memories 业务记忆分离）
-- 用途：仅收录「前端、后端、数据库结构」相关变更的摘要，便于语义检索与回顾。
-- 向量：768 维，与 business.memories 一致；由 Ollama 中文 embedding 生成（见 scripts/upsert_design_memory.py）。
-- ============================================================

BEGIN;

CREATE TABLE IF NOT EXISTS business.design_memories (
    id              integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title           character varying(200) NOT NULL,
    content         text NOT NULL,
    change_layers   text[] NOT NULL,
    source_ref      text,
    tags            text[],
    embedding       vector(768),
    is_current      boolean NOT NULL DEFAULT true,
    created_by      integer REFERENCES business.employees (id) ON DELETE SET NULL,
    created_at      timestamp with time zone NOT NULL DEFAULT now(),
    updated_at      timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT design_memories_layers_check CHECK (
        change_layers <@ ARRAY['frontend', 'backend', 'database']::text[]
        AND cardinality(change_layers) >= 1
    )
);

COMMENT ON TABLE business.design_memories IS
'设计过程记忆：仅存与前端、后端、库表结构相关的变更摘要，供向量检索与团队回顾；不包含单笔业务事实（业务见 business.memories）。';

COMMENT ON COLUMN business.design_memories.title IS '短标题，便于列表展示与人工扫读。';
COMMENT ON COLUMN business.design_memories.content IS '写入向量库的完整中文叙述（背景、改动点、原因、影响面）；检索主字段。';
COMMENT ON COLUMN business.design_memories.change_layers IS
'变更层面，取子集于 {frontend, backend, database}，至少一项；用于过滤与统计。';
COMMENT ON COLUMN business.design_memories.source_ref IS
'可追溯引用：文件路径、迁移脚本名、PR 链接、commit 短哈希等，逗号或换行分隔均可。';
COMMENT ON COLUMN business.design_memories.tags IS '可选补充标签，如 auth、vector、vue。';
COMMENT ON COLUMN business.design_memories.embedding IS '768 维语义向量；与 shaw/dmeta-embedding-zh 等模型维度需一致。';
COMMENT ON COLUMN business.design_memories.is_current IS
'若同一主题被新记录取代，可将旧记录置 false，避免检索噪声（也可保留全历史仅用时间排序）。';

CREATE INDEX IF NOT EXISTS idx_design_memories_created_at
    ON business.design_memories (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_design_memories_layers
    ON business.design_memories USING gin (change_layers);

CREATE INDEX IF NOT EXISTS idx_design_memories_embedding
    ON business.design_memories
    USING hnsw (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;

COMMIT;
