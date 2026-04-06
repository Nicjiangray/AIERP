-- V14-A：将 collecting 流程步骤从 workflows.context_schema.steps 拆到 business.workflow_steps
-- 执行：psql -d aierp -f constitution/refactor_workflow_steps.sql
-- 填充 fields：psql -d aierp -f constitution/sync_workflow_step_fields.sql
--            然后 SELECT meta.sync_workflow_step_fields(true);
BEGIN;

-- 1) 旧表（预置 SQL / step_type 等）备份到 workflow_steps_legacy，供 engine/workflow.run_workflow 使用
DO $mig$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'business' AND table_name = 'workflow_steps'
  ) THEN
    EXECUTE 'DROP TABLE IF EXISTS business.workflow_steps_legacy';
    EXECUTE 'CREATE TABLE business.workflow_steps_legacy AS TABLE business.workflow_steps WITH DATA';
    EXECUTE 'DROP TABLE business.workflow_steps';
  ELSE
    -- 库中原本无旧表时仍建空 legacy，供 engine.workflow / seed 使用
    EXECUTE $ddl$
    CREATE TABLE IF NOT EXISTS business.workflow_steps_legacy (
      id SERIAL PRIMARY KEY,
      workflow_intent VARCHAR NOT NULL,
      step_order INTEGER NOT NULL,
      step_name VARCHAR,
      step_type VARCHAR NOT NULL,
      action VARCHAR,
      sql_template TEXT,
      prompt_template TEXT,
      input_keys TEXT,
      output_key TEXT,
      on_fail VARCHAR DEFAULT 'stop',
      deleted_at TIMESTAMP NULL
    );
    $ddl$;
  END IF;
END
$mig$;

-- CREATE AS 不复制唯一约束；供 seed 的 ON CONFLICT (workflow_intent, step_order)
DO $idx$
BEGIN
  IF to_regclass('business.workflow_steps_legacy') IS NOT NULL THEN
    EXECUTE 'CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_steps_legacy_intent_order ON business.workflow_steps_legacy (workflow_intent, step_order)';
  END IF;
END
$idx$;

COMMENT ON TABLE business.workflow_steps_legacy IS
  '旧版 workflow_steps（step_type/query_sql 等），供 run_workflow 读取；主路径 collecting 已改用新版 workflow_steps。';

-- 2) 新版：每步一行，关联意图与业务表；fields 由 meta.sync_workflow_step_fields() 填充
CREATE TABLE IF NOT EXISTS business.workflow_steps (
  id                SERIAL PRIMARY KEY,
  workflow_intent   VARCHAR NOT NULL,
  seq               INTEGER NOT NULL,
  action            VARCHAR NOT NULL,
  label             VARCHAR NOT NULL,
  table_name        VARCHAR,
  instruction       TEXT,
  tool              VARCHAR,
  fields            JSONB DEFAULT '[]'::jsonb,
  validation_rules JSONB DEFAULT '[]'::jsonb,
  created_at        TIMESTAMPTZ DEFAULT now(),
  updated_at        TIMESTAMPTZ DEFAULT now(),
  CONSTRAINT uq_workflow_steps_intent_seq UNIQUE (workflow_intent, seq)
);

COMMENT ON TABLE business.workflow_steps IS
  'Collecting 参考流程步骤：按意图多行存储（lookup/collect/submit），fields 由 meta.sync_workflow_step_fields 从 meta_fields 同步。';

CREATE INDEX IF NOT EXISTS idx_workflow_steps_intent ON business.workflow_steps (workflow_intent);

-- 3) 从原 add_workflow_context_schema 中各意图的 steps 迁移（fields 留空）
INSERT INTO business.workflow_steps (workflow_intent, seq, action, label, table_name, instruction, tool) VALUES
('create_simple_entity', 1, 'collect', '客户主数据（customers）', 'customers',
 $t$用户要新建或操作客户时：用 query_db 核对名称/编码；字段齐全后 write_entity(table_name=customers)。$t$, NULL),
('create_simple_entity', 2, 'collect', '联系人（contacts）', 'contacts',
 $t$新建联系人：收集 contacts 字段；write_entity(table_name=contacts)。$t$, NULL),
('create_simple_entity', 3, 'collect', '产品（products）', 'products',
 $t$新建产品：收集 products 字段；product_name 为产品名称列；write_entity(table_name=products)。$t$, NULL),
('create_simple_entity', 4, 'submit', '提交新建预览', NULL, NULL, 'write_entity')
ON CONFLICT (workflow_intent, seq) DO NOTHING;

INSERT INTO business.workflow_steps (workflow_intent, seq, action, label, table_name, instruction, tool) VALUES
('update_simple_entity', 1, 'lookup', '查找客户', 'customers',
 $t$用 query_db 在 business.customers 中按公司名称或 short_name 等定位记录，结果须唯一后再 update_entity。$t$, NULL),
('update_simple_entity', 2, 'lookup', '查找联系人', 'contacts',
 $t$修改联系人前用 query_db 确认唯一记录与 id。$t$, NULL),
('update_simple_entity', 3, 'lookup', '查找产品', 'products',
 $t$修改产品前用 query_db 确认唯一记录与 id；产品名称列为 product_name。$t$, NULL),
('update_simple_entity', 4, 'collect', '更新字段（customers）', 'customers',
 $t$用户要改客户时：对 customers 使用 update_entity（record_id 须来自 query_db 唯一结果）。$t$, NULL),
('update_simple_entity', 5, 'collect', '更新字段（contacts）', 'contacts',
 $t$用户要改联系人时：对 contacts 使用 update_entity。$t$, NULL),
('update_simple_entity', 6, 'collect', '更新字段（products）', 'products',
 $t$用户要改产品时：对 products 使用 update_entity；产品名称列为 product_name。$t$, NULL),
('update_simple_entity', 7, 'submit', '提交更新预览', NULL, NULL, 'update_entity')
ON CONFLICT (workflow_intent, seq) DO NOTHING;

INSERT INTO business.workflow_steps (workflow_intent, seq, action, label, table_name, instruction, tool) VALUES
('delete_simple_entity', 1, 'lookup', '查找待删记录（客户）', 'customers',
 $t$用 query_db 确认目标行唯一且含 id。$t$, NULL),
('delete_simple_entity', 2, 'lookup', '查找待删记录（联系人）', 'contacts', $t$同上。$t$, NULL),
('delete_simple_entity', 3, 'lookup', '查找待删记录（产品）', 'products', $t$同上。$t$, NULL),
('delete_simple_entity', 4, 'submit', '提交软删预览', NULL, NULL, 'delete_entity')
ON CONFLICT (workflow_intent, seq) DO NOTHING;

INSERT INTO business.workflow_steps (workflow_intent, seq, action, label, table_name, instruction, tool) VALUES
('query_master_data', 1, 'lookup', '客户与联系人', 'customers',
 $t$只读：可查询 customers、contacts 等；JOIN 时参考字段名与关联。$t$, NULL),
('query_master_data', 2, 'lookup', '产品', 'products',
 $t$只读：products.product_name 为产品名称列。$t$, NULL),
('query_master_data', 3, 'lookup', '合同与明细', 'contracts',
 $t$只读：contracts 与 contract_items、customers 等关联查询。$t$, NULL),
('query_master_data', 4, 'lookup', '合同模板与变更', 'contract_templates',
 $t$只读：contract_templates、contract_changes 等。$t$, NULL)
ON CONFLICT (workflow_intent, seq) DO NOTHING;

INSERT INTO business.workflow_steps (workflow_intent, seq, action, label, table_name, instruction, tool) VALUES
('create_contract', 1, 'lookup', '确认客户', 'customers',
 $t$用 ILIKE 模糊匹配客户名称；取得 customer_id 与客户名称（作 customer_name）。$t$, NULL),
('create_contract', 2, 'collect', '收集合同主表', 'contracts',
 $t$收集 contracts 主表信息（勿填 contract_no、total_amount 等系统字段）。$t$, NULL),
('create_contract', 3, 'collect', '收集合同明细', 'contract_items',
 $t$至少一行明细：product_name、quantity、unit_price；勿传 amount。$t$, NULL),
('create_contract', 4, 'lookup', '核对产品', 'products',
 $t$用 query_db 查 products 填写 product_code、product_id；名称列 product_name。$t$, NULL),
('create_contract', 5, 'submit', '提交合同预览', NULL, NULL, 'submit_contract')
ON CONFLICT (workflow_intent, seq) DO NOTHING;

INSERT INTO business.workflow_steps (workflow_intent, seq, action, label, table_name, instruction, tool) VALUES
('update_contract', 1, 'lookup', '定位当前合同', 'contracts',
 $t$用 query_db 拉取 contracts 与 contract_items 当前值；确认 contract_id。$t$, NULL),
('update_contract', 2, 'collect', '收集变更内容', 'contract_items',
 $t$弄清主表字段与明细增删改；变更原因 reason 必填。$t$, NULL),
('update_contract', 3, 'submit', '提交变更预览', NULL, NULL, 'update_contract')
ON CONFLICT (workflow_intent, seq) DO NOTHING;

INSERT INTO business.workflow_steps (workflow_intent, seq, action, label, table_name, instruction, tool) VALUES
('delete_contract', 1, 'lookup', '定位待删合同', 'contracts',
 $t$用 query_db 确认目标合同唯一。$t$, NULL),
('delete_contract', 2, 'collect', '确认删除原因', 'contracts',
 $t$向用户确认删除原因，explanation 必填。$t$, NULL),
('delete_contract', 3, 'submit', '提交删除预览', NULL, NULL, 'delete_entity')
ON CONFLICT (workflow_intent, seq) DO NOTHING;

COMMIT;
