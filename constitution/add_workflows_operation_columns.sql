-- 意图识别：操作类型与可关联业务表（动态上下文注入）
BEGIN;

ALTER TABLE business.workflows
    ADD COLUMN IF NOT EXISTS operation_type TEXT,
    ADD COLUMN IF NOT EXISTS target_tables TEXT[];

COMMENT ON COLUMN business.workflows.operation_type IS 'read / create / update / delete 等，供意图识别目录展示';
COMMENT ON COLUMN business.workflows.target_tables IS '该 workflow 可指向的业务表英文名数组';

UPDATE business.workflows
SET operation_type = 'read',
    target_tables = ARRAY['customers', 'products', 'contacts']
WHERE intent = 'query_master_data';

UPDATE business.workflows
SET operation_type = 'create',
    target_tables = ARRAY['customers', 'products', 'contacts']
WHERE intent = 'create_simple_entity';

UPDATE business.workflows
SET operation_type = 'update',
    target_tables = ARRAY['customers', 'products', 'contacts']
WHERE intent = 'update_simple_entity';

COMMIT;
