-- 校准 meta.meta_fields.ask_if_missing（V16a）
-- ask_if_missing = true：需 LLM 从用户处收集的业务字段
-- 执行：psql -d aierp -f constitution/v16a_fix_ask_if_missing.sql

BEGIN;

-- contracts
UPDATE meta.meta_fields SET ask_if_missing = false
WHERE table_name = 'contracts' AND field_name IN (
  'id', 'created_at', 'updated_at', 'deleted_at', 'created_by',
  'contract_no', 'total_amount', 'status', 'customer_id', 'template_id'
);
UPDATE meta.meta_fields SET ask_if_missing = true
WHERE table_name = 'contracts' AND field_name IN (
  'customer_name', 'signed_date', 'notes', 'payment_terms', 'currency', 'delivery_date'
);

-- contract_items
UPDATE meta.meta_fields SET ask_if_missing = false
WHERE table_name = 'contract_items' AND field_name IN (
  'id', 'deleted_at',
  'contract_id', 'product_id', 'amount'
);
UPDATE meta.meta_fields SET ask_if_missing = true
WHERE table_name = 'contract_items' AND field_name IN (
  'product_code', 'product_name', 'color', 'spec', 'unit', 'quantity', 'unit_price', 'notes'
);

-- customers
UPDATE meta.meta_fields SET ask_if_missing = false
WHERE table_name = 'customers' AND field_name IN (
  'id', 'created_at', 'updated_at', 'deleted_at', 'created_by',
  'status', 'source_event', 'template_id'
);
UPDATE meta.meta_fields SET ask_if_missing = true
WHERE table_name = 'customers' AND field_name IN (
  'customer_code', 'name', 'short_name', 'country', 'province', 'city', 'district', 'address',
  'currency', 'notes', 'payment_terms', 'credit_limit', 'tax_id', 'invoice_type'
);

-- contacts
UPDATE meta.meta_fields SET ask_if_missing = false
WHERE table_name = 'contacts' AND field_name IN (
  'id', 'created_at', 'updated_at', 'deleted_at',
  'company_id', 'status', 'source_event'
);
UPDATE meta.meta_fields SET ask_if_missing = true
WHERE table_name = 'contacts' AND field_name IN (
  'company_type', 'name', 'title', 'phone', 'email', 'notes', 'is_primary', 'wechat', 'department'
);

-- products
UPDATE meta.meta_fields SET ask_if_missing = false
WHERE table_name = 'products' AND field_name IN (
  'id', 'created_at', 'updated_at', 'deleted_at', 'created_by',
  'status', 'source_event'
);
UPDATE meta.meta_fields SET ask_if_missing = true
WHERE table_name = 'products' AND field_name IN (
  'product_code', 'product_name', 'category', 'material', 'composition', 'color', 'width_cm', 'weight_gsm',
  'process_desc', 'unit', 'price', 'currency', 'min_order_qty', 'quality_grade', 'notes'
);

COMMIT;
