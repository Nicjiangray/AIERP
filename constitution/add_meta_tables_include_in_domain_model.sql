-- meta.meta_tables：是否纳入 LLM 领域模型（影子表、提示词等）
-- 执行：psql -d aierp -f constitution/add_meta_tables_include_in_domain_model.sql
-- 之后：CALL meta.sync_constitution(); 已包含新列的 INSERT 逻辑（见 constitution/sync_constitution.sql）

ALTER TABLE meta.meta_tables
    ADD COLUMN IF NOT EXISTS include_in_domain_model BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN meta.meta_tables.include_in_domain_model IS
    '是否纳入 LLM 领域模型：true 时进入 schema_shadow 等提示词；meta 宪法表一般为 false，business 业务表默认 true，可在库内微调（如会话/帧表改为 false）。';

-- 按物理库位置初始化：business 表默认 true，其余（含 meta）false
UPDATE meta.meta_tables mt
SET include_in_domain_model = EXISTS (
    SELECT 1
    FROM information_schema.tables t
    WHERE t.table_name = mt.table_name
      AND t.table_schema = 'business'
      AND t.table_type = 'BASE TABLE'
)
WHERE mt.deleted_at IS NULL;
