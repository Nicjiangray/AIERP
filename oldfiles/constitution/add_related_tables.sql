-- meta.meta_tables：声明表间关系，供 collecting / query_db JOIN 提示
-- 执行：psql -d aierp -f constitution/add_related_tables.sql

ALTER TABLE meta.meta_tables
    ADD COLUMN IF NOT EXISTS related_tables JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN meta.meta_tables.related_tables IS
    '与本表相关的外键/软关联，JSON 数组：[{table,fk,type,note?}]，type 如 belongs_to/has_many/soft_reference';

-- contracts
UPDATE meta.meta_tables
SET related_tables = '[
  {"table": "customers", "fk": "customer_id", "type": "belongs_to"},
  {"table": "contract_items", "fk": "contract_id", "type": "has_many"},
  {"table": "contract_changes", "fk": "contract_id", "type": "has_many"}
]'::jsonb
WHERE table_name = 'contracts';

-- contract_items
UPDATE meta.meta_tables
SET related_tables = '[
  {"table": "contracts", "fk": "contract_id", "type": "belongs_to"},
  {"table": "products", "fk": "product_name", "type": "soft_reference", "note": "与 products.product_name 文本对应，非强制外键"}
]'::jsonb
WHERE table_name = 'contract_items';

-- contract_changes
UPDATE meta.meta_tables
SET related_tables = '[
  {"table": "contracts", "fk": "contract_id", "type": "belongs_to"}
]'::jsonb
WHERE table_name = 'contract_changes';

-- contacts
UPDATE meta.meta_tables
SET related_tables = '[
  {"table": "customers", "fk": "customer_id", "type": "belongs_to"}
]'::jsonb
WHERE table_name = 'contacts';
