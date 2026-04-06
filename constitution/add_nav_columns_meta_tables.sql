-- ============================================================
-- 阶段 0 + 1：meta.meta_tables 导航配置字段 + 种子数据
-- nav_section: design=系统设计, prep=系统准备, run=系统运行
-- nav_order: 同区内排序，越小越靠前
-- nav_parent_table: 主副表时填父表 table_name（当前阶段多为 NULL，仅占位）
-- 执行后可在前端/API 用 ORDER BY nav_section, nav_order
-- ============================================================

BEGIN;

ALTER TABLE meta.meta_tables
    ADD COLUMN IF NOT EXISTS nav_section character varying(20),
    ADD COLUMN IF NOT EXISTS nav_order integer NOT NULL DEFAULT 1000,
    ADD COLUMN IF NOT EXISTS nav_parent_table character varying(50);

COMMENT ON COLUMN meta.meta_tables.nav_section IS
'导航大区分组：design=系统设计；prep=系统准备（主数据等）；run=系统运行（业务流水）。与前端右侧三大块对应。';

COMMENT ON COLUMN meta.meta_tables.nav_order IS
'同 nav_section 内排序，数值越小越靠前。';

COMMENT ON COLUMN meta.meta_tables.nav_parent_table IS
'可选：子表指向父表 table_name（与 meta.meta_tables.table_name 对应），用于右侧子菜单；无父子关系时为 NULL。';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'meta_tables_nav_section_check'
    ) THEN
        ALTER TABLE meta.meta_tables
            ADD CONSTRAINT meta_tables_nav_section_check
            CHECK (nav_section IS NULL OR nav_section IN ('design', 'prep', 'run'));
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'meta_tables_nav_parent_fkey'
    ) THEN
        ALTER TABLE meta.meta_tables
            ADD CONSTRAINT meta_tables_nav_parent_fkey
            FOREIGN KEY (nav_parent_table)
            REFERENCES meta.meta_tables (table_name)
            ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_meta_tables_nav
    ON meta.meta_tables (nav_section, nav_order);

-- ------------------------------------------------------------
-- 种子：现有表归类（后续新增表可由 sync_constitution + 手工补 nav）
-- ------------------------------------------------------------

UPDATE meta.meta_tables SET nav_section = 'design', nav_order = 10,  nav_parent_table = NULL WHERE table_name = 'meta_tables';
UPDATE meta.meta_tables SET nav_section = 'design', nav_order = 20,  nav_parent_table = NULL WHERE table_name = 'meta_fields';
UPDATE meta.meta_tables SET nav_section = 'design', nav_order = 30,  nav_parent_table = NULL WHERE table_name = 'meta_rules';
UPDATE meta.meta_tables SET nav_section = 'design', nav_order = 40,  nav_parent_table = NULL WHERE table_name = 'meta_rule_steps';
UPDATE meta.meta_tables SET nav_section = 'design', nav_order = 50,  nav_parent_table = NULL WHERE table_name = 'ai_rules';
UPDATE meta.meta_tables SET nav_section = 'design', nav_order = 60,  nav_parent_table = NULL WHERE table_name = 'approval_rules';
UPDATE meta.meta_tables SET nav_section = 'design', nav_order = 70,  nav_parent_table = NULL WHERE table_name = 'procedures';
UPDATE meta.meta_tables SET nav_section = 'design', nav_order = 80,  nav_parent_table = NULL WHERE table_name = 'procedure_logs';

UPDATE meta.meta_tables SET nav_section = 'prep',   nav_order = 10,  nav_parent_table = NULL WHERE table_name = 'employees';
UPDATE meta.meta_tables SET nav_section = 'prep',   nav_order = 20,  nav_parent_table = NULL WHERE table_name = 'contacts';
UPDATE meta.meta_tables SET nav_section = 'prep',   nav_order = 30,  nav_parent_table = NULL WHERE table_name = 'customers';
UPDATE meta.meta_tables SET nav_section = 'prep',   nav_order = 40,  nav_parent_table = NULL WHERE table_name = 'suppliers';
UPDATE meta.meta_tables SET nav_section = 'prep',   nav_order = 50,  nav_parent_table = NULL WHERE table_name = 'products';
UPDATE meta.meta_tables SET nav_section = 'prep',   nav_order = 60,  nav_parent_table = NULL WHERE table_name = 'term_mappings';

UPDATE meta.meta_tables SET nav_section = 'run',    nav_order = 10,  nav_parent_table = NULL WHERE table_name = 'events';
UPDATE meta.meta_tables SET nav_section = 'run',    nav_order = 20,  nav_parent_table = NULL WHERE table_name = 'event_steps';
UPDATE meta.meta_tables SET nav_section = 'run',    nav_order = 30,  nav_parent_table = NULL WHERE table_name = 'contracts';
UPDATE meta.meta_tables SET nav_section = 'run',    nav_order = 40,  nav_parent_table = NULL WHERE table_name = 'contract_terms_log';
UPDATE meta.meta_tables SET nav_section = 'run',    nav_order = 50,  nav_parent_table = NULL WHERE table_name = 'supplier_quotes';
UPDATE meta.meta_tables SET nav_section = 'run',    nav_order = 60,  nav_parent_table = NULL WHERE table_name = 'purchase_order_items';
UPDATE meta.meta_tables SET nav_section = 'run',    nav_order = 70,  nav_parent_table = NULL WHERE table_name = 'purchase_receipts';
UPDATE meta.meta_tables SET nav_section = 'run',    nav_order = 80,  nav_parent_table = NULL WHERE table_name = 'shipments';
UPDATE meta.meta_tables SET nav_section = 'run',    nav_order = 90,  nav_parent_table = NULL WHERE table_name = 'shipment_items';
UPDATE meta.meta_tables SET nav_section = 'run',    nav_order = 100, nav_parent_table = NULL WHERE table_name = 'inventory';
UPDATE meta.meta_tables SET nav_section = 'run',    nav_order = 110, nav_parent_table = NULL WHERE table_name = 'inventory_ledger';
UPDATE meta.meta_tables SET nav_section = 'run',    nav_order = 120, nav_parent_table = NULL WHERE table_name = 'attachments';
UPDATE meta.meta_tables SET nav_section = 'run',    nav_order = 130, nav_parent_table = NULL WHERE table_name = 'memories';
UPDATE meta.meta_tables SET nav_section = 'run',    nav_order = 140, nav_parent_table = NULL WHERE table_name = 'people_observations';
UPDATE meta.meta_tables SET nav_section = 'run',    nav_order = 150, nav_parent_table = NULL WHERE table_name = 'product_specs_log';

COMMIT;

-- 验证
SELECT nav_section, nav_order, table_name, table_cn
FROM meta.meta_tables
ORDER BY nav_section NULLS LAST, nav_order, table_name;

-- ------------------------------------------------------------
-- 后续：若执行 CALL meta.sync_constitution(); 后在 business 等 schema 出现新表（如 user_accounts、
-- design_memories），可手工补 meta.meta_tables 导航，例如：
-- UPDATE meta.meta_tables SET nav_section = 'design', nav_order = 85
--   WHERE table_name = 'user_accounts';
-- UPDATE meta.meta_tables SET nav_section = 'design', nav_order = 86
--   WHERE table_name = 'design_memories';
-- ------------------------------------------------------------
