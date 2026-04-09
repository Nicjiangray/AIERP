-- V15：填充 workflow_steps 的 input_fields / output_fields / depends_on（通用引擎燃料）
-- 另：为 write 步骤写入 validation_rules.composite，供引擎 for-loop 识别（非 pending_submit.mode）
-- 前置：已执行 constitution/v15_atomic_engine.sql（lookup→query、submit→write、扩展列）
-- 执行：psql -d aierp -f constitution/v15_workflow_steps_io.sql

BEGIN;

-- ── create_simple_entity（4 步：三表 collect + write）────────────────────────────
UPDATE business.workflow_steps SET
  input_fields = '[{"field":"entity_data","source":"user"}]'::jsonb,
  output_fields = '[{"field":"table_name"},{"field":"entity_data"}]'::jsonb,
  depends_on = NULL
WHERE workflow_intent = 'create_simple_entity' AND seq = 1;

UPDATE business.workflow_steps SET
  input_fields = '[{"field":"entity_data","source":"user"}]'::jsonb,
  output_fields = '[{"field":"table_name"},{"field":"entity_data"}]'::jsonb,
  depends_on = NULL
WHERE workflow_intent = 'create_simple_entity' AND seq = 2;

UPDATE business.workflow_steps SET
  input_fields = '[{"field":"entity_data","source":"user"}]'::jsonb,
  output_fields = '[{"field":"table_name"},{"field":"entity_data"}]'::jsonb,
  depends_on = NULL
WHERE workflow_intent = 'create_simple_entity' AND seq = 3;

UPDATE business.workflow_steps SET
  input_fields = '[]'::jsonb,
  output_fields = '[]'::jsonb,
  depends_on = ARRAY[1,2,3],
  validation_rules = '[{"composite":"simple_insert"}]'::jsonb
WHERE workflow_intent = 'create_simple_entity' AND seq = 4;

-- ── update_simple_entity（7 步）────────────────────────────────────────────────
UPDATE business.workflow_steps SET
  input_fields = '[{"field":"entity_identifier","source":"user"}]'::jsonb,
  output_fields = '[{"field":"record_id"},{"field":"table_name"}]'::jsonb,
  depends_on = NULL
WHERE workflow_intent = 'update_simple_entity' AND seq IN (1,2,3);

UPDATE business.workflow_steps SET
  input_fields = '[{"field":"changes","source":"user"}]'::jsonb,
  output_fields = '[{"field":"changes"}]'::jsonb,
  depends_on = ARRAY[1]
WHERE workflow_intent = 'update_simple_entity' AND seq = 4;

UPDATE business.workflow_steps SET
  input_fields = '[{"field":"changes","source":"user"}]'::jsonb,
  output_fields = '[{"field":"changes"}]'::jsonb,
  depends_on = ARRAY[2]
WHERE workflow_intent = 'update_simple_entity' AND seq = 5;

UPDATE business.workflow_steps SET
  input_fields = '[{"field":"changes","source":"user"}]'::jsonb,
  output_fields = '[{"field":"changes"}]'::jsonb,
  depends_on = ARRAY[3]
WHERE workflow_intent = 'update_simple_entity' AND seq = 6;

UPDATE business.workflow_steps SET
  input_fields = '[]'::jsonb,
  output_fields = '[]'::jsonb,
  depends_on = ARRAY[1,2,3,4,5,6],
  validation_rules = '[{"composite":"simple_update"}]'::jsonb
WHERE workflow_intent = 'update_simple_entity' AND seq = 7;

-- ── delete_simple_entity（4 步）────────────────────────────────────────────────
UPDATE business.workflow_steps SET
  input_fields = '[{"field":"entity_identifier","source":"user"}]'::jsonb,
  output_fields = '[{"field":"record_id"},{"field":"table_name"}]'::jsonb,
  depends_on = NULL
WHERE workflow_intent = 'delete_simple_entity' AND seq IN (1,2,3);

UPDATE business.workflow_steps SET
  input_fields = '[]'::jsonb,
  output_fields = '[]'::jsonb,
  depends_on = ARRAY[1,2,3],
  validation_rules = '[{"composite":"simple_delete"}]'::jsonb
WHERE workflow_intent = 'delete_simple_entity' AND seq = 4;

-- ── query_master_data（4 步 query，无 write）────────────────────────────────────
UPDATE business.workflow_steps SET
  input_fields = '[{"field":"query_description","source":"user"}]'::jsonb,
  output_fields = '[{"field":"query_result"}]'::jsonb,
  depends_on = NULL
WHERE workflow_intent = 'query_master_data' AND seq IN (1,2,3,4);

-- ── create_contract（5 步；与当前表结构一致：主表/明细顺序与产品核对）────────────
UPDATE business.workflow_steps SET
  input_fields = '[{"field":"customer_name","source":"user"}]'::jsonb,
  output_fields = '[{"field":"customer_id"},{"field":"customer_name"}]'::jsonb,
  depends_on = NULL
WHERE workflow_intent = 'create_contract' AND seq = 1;

UPDATE business.workflow_steps SET
  input_fields = '[{"field":"contract_type","source":"user","required":true},{"field":"signing_date","source":"user"},{"field":"notes","source":"user"}]'::jsonb,
  output_fields = '[]'::jsonb,
  depends_on = ARRAY[1]
WHERE workflow_intent = 'create_contract' AND seq = 2;

UPDATE business.workflow_steps SET
  input_fields = '[{"field":"quantity","source":"user","required":true}]'::jsonb,
  output_fields = '[{"field":"quantity"}]'::jsonb,
  depends_on = ARRAY[1,2]
WHERE workflow_intent = 'create_contract' AND seq = 3;

UPDATE business.workflow_steps SET
  input_fields = '[{"field":"product_name","source":"user"}]'::jsonb,
  output_fields = '[{"field":"product_id"},{"field":"product_name"},{"field":"unit_price"}]'::jsonb,
  depends_on = NULL
WHERE workflow_intent = 'create_contract' AND seq = 4;

UPDATE business.workflow_steps SET
  input_fields = '[]'::jsonb,
  output_fields = '[{"field":"contract_id"}]'::jsonb,
  depends_on = ARRAY[1,2,3,4],
  validation_rules = '[{"composite":"create_contract"}]'::jsonb
WHERE workflow_intent = 'create_contract' AND seq = 5;

-- ── update_contract（3 步）─────────────────────────────────────────────────────
UPDATE business.workflow_steps SET
  input_fields = '[{"field":"contract_no","source":"user"}]'::jsonb,
  output_fields = '[{"field":"contract_id"},{"field":"snapshot_before"}]'::jsonb,
  depends_on = NULL
WHERE workflow_intent = 'update_contract' AND seq = 1;

UPDATE business.workflow_steps SET
  input_fields = '[{"field":"changes","source":"user"},{"field":"reason","source":"user","required":true}]'::jsonb,
  output_fields = '[{"field":"changes"},{"field":"reason"}]'::jsonb,
  depends_on = ARRAY[1]
WHERE workflow_intent = 'update_contract' AND seq = 2;

UPDATE business.workflow_steps SET
  input_fields = '[]'::jsonb,
  output_fields = '[]'::jsonb,
  depends_on = ARRAY[1,2],
  validation_rules = '[{"composite":"update_contract"}]'::jsonb
WHERE workflow_intent = 'update_contract' AND seq = 3;

-- ── delete_contract（3 步）──────────────────────────────────────────────────────
UPDATE business.workflow_steps SET
  input_fields = '[{"field":"contract_no","source":"user"}]'::jsonb,
  output_fields = '[{"field":"contract_id"}]'::jsonb,
  depends_on = NULL
WHERE workflow_intent = 'delete_contract' AND seq = 1;

UPDATE business.workflow_steps SET
  input_fields = '[{"field":"reason","source":"user","required":true}]'::jsonb,
  output_fields = '[{"field":"reason"}]'::jsonb,
  depends_on = ARRAY[1]
WHERE workflow_intent = 'delete_contract' AND seq = 2;

UPDATE business.workflow_steps SET
  input_fields = '[]'::jsonb,
  output_fields = '[]'::jsonb,
  depends_on = ARRAY[1,2],
  validation_rules = '[{"composite":"delete_contract"}]'::jsonb
WHERE workflow_intent = 'delete_contract' AND seq = 3;

COMMIT;
