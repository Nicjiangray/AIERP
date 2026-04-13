-- V36：删除合同/采购单主表上的交期列；交期仅以子表 contract_items / purchase_order_items 的 expected_delivery_date 为准
-- 执行：psql -U nicjiang -d aierp -f constitution/v36_drop_main_table_delivery_columns.sql
-- 完成后：python scripts/sync_meta_pipeline.py（或至少 CALL meta.sync_constitution(); 与 sync_schema_shadow）

BEGIN;

-- 子表列（与 v12 一致，幂等）
ALTER TABLE business.contract_items
    ADD COLUMN IF NOT EXISTS expected_delivery_date DATE;
ALTER TABLE business.purchase_order_items
    ADD COLUMN IF NOT EXISTS expected_delivery_date DATE;

-- 删主表列前，把仍只在主表上的交期写入明细行（避免丢数据）
UPDATE business.contract_items ci
SET expected_delivery_date = c.delivery_date
FROM business.contracts c
WHERE ci.contract_id = c.id
  AND ci.expected_delivery_date IS NULL
  AND c.delivery_date IS NOT NULL;

UPDATE business.purchase_order_items poi
SET expected_delivery_date = po.expected_delivery_date
FROM business.purchase_orders po
WHERE poi.po_id = po.id
  AND poi.expected_delivery_date IS NULL
  AND po.expected_delivery_date IS NOT NULL;

ALTER TABLE business.contracts
    DROP COLUMN IF EXISTS delivery_date;

ALTER TABLE business.purchase_orders
    DROP COLUMN IF EXISTS expected_delivery_date;

COMMIT;

CALL meta.sync_constitution();
