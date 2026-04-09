-- products：列 name → product_name（与 LLM / 合同明细 product_name 命名一致）
-- 执行：psql -d aierp -f constitution/rename_products_name_to_product_name.sql

BEGIN;

ALTER TABLE business.products RENAME COLUMN name TO product_name;

UPDATE meta.meta_fields
SET field_name = 'product_name'
WHERE table_name = 'products' AND field_name = 'name';

COMMENT ON COLUMN business.products.product_name IS '产品名称';

COMMIT;
