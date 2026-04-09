-- 简单实体 / 复杂实体标记（供 collecting、LLM 选表白名单）
ALTER TABLE meta.meta_tables
ADD COLUMN IF NOT EXISTS entity_type varchar(20) DEFAULT 'simple';

UPDATE meta.meta_tables
SET entity_type = 'simple'
WHERE table_name IN ('customers', 'products', 'contacts');

UPDATE meta.meta_tables
SET entity_type = 'complex'
WHERE table_name IN ('contracts', 'contract_items');
