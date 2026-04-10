-- 收付款 + 分配 + 库存 + 交期下钻子表 + contracts.received_amount + shipping_note_items.inventory_id
-- 执行：psql -U nicjiang -d aierp -f constitution/v12_payments_inventory_delivery_fix.sql
-- 完成后脚本末尾含：CALL meta.sync_constitution()、include_in_domain_model、导航更新；再运行 python scripts/sync_meta_pipeline.py

BEGIN;

-- =============================================================================
-- 一、交期：子表增加 expected_delivery_date，并从主表回填（主表字段保留，标注弃用）
-- =============================================================================
ALTER TABLE business.contract_items
    ADD COLUMN IF NOT EXISTS expected_delivery_date DATE;

COMMENT ON COLUMN business.contract_items.expected_delivery_date IS '本行预计交货日期';

ALTER TABLE business.purchase_order_items
    ADD COLUMN IF NOT EXISTS expected_delivery_date DATE;

COMMENT ON COLUMN business.purchase_order_items.expected_delivery_date IS '本行预计交货日期';

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

COMMENT ON COLUMN business.contracts.delivery_date IS
'【已弃用】主表统一交期；按行交期请使用 contract_items.expected_delivery_date。';

COMMENT ON COLUMN business.purchase_orders.expected_delivery_date IS
'【已弃用】主表统一交期；按行交期请使用 purchase_order_items.expected_delivery_date。';

-- =============================================================================
-- 二、business.payments
-- =============================================================================
CREATE TABLE business.payments (
    id                 SERIAL PRIMARY KEY,
    payment_no         VARCHAR(50)  NOT NULL UNIQUE,
    direction          VARCHAR(10)  NOT NULL,
    counterparty_type  VARCHAR(20)  NOT NULL,
    counterparty_id    INTEGER      NOT NULL,
    counterparty_name  VARCHAR(500),
    amount             NUMERIC(15, 2) NOT NULL,
    currency           VARCHAR(10)  NOT NULL DEFAULT 'CNY',
    payment_date       DATE         NOT NULL,
    payment_method     VARCHAR(100),
    bank_ref           VARCHAR(200),
    payment_type       VARCHAR(50),
    exchange_rate      NUMERIC(10, 6) NOT NULL DEFAULT 1,
    amount_local       NUMERIC(15, 2),
    notes              TEXT,
    created_by         INTEGER REFERENCES business.employees (id) ON DELETE SET NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT payments_direction_check
        CHECK (direction IN ('in', 'out')),
    CONSTRAINT payments_counterparty_type_check
        CHECK (counterparty_type IN ('customer', 'supplier'))
);

COMMENT ON TABLE business.payments IS '收付款流水：direction in=收款 out=付款';
COMMENT ON COLUMN business.payments.payment_no IS '流水号，如 PAY-202604-001';
COMMENT ON COLUMN business.payments.direction IS 'in 收款 / out 付款';
COMMENT ON COLUMN business.payments.counterparty_type IS 'customer 或 supplier（counterparty_id 语义依此区分，不设多态 FK）';
COMMENT ON COLUMN business.payments.payment_type IS '如 deposit、progress、final、monthly_settlement、refund';

CREATE INDEX IF NOT EXISTS idx_payments_payment_date ON business.payments (payment_date);
CREATE INDEX IF NOT EXISTS idx_payments_counterparty ON business.payments (counterparty_type, counterparty_id);

-- =============================================================================
-- 三、business.payment_allocations
-- =============================================================================
CREATE TABLE business.payment_allocations (
    id           SERIAL PRIMARY KEY,
    payment_id   INTEGER NOT NULL REFERENCES business.payments (id) ON DELETE CASCADE,
    target_type  VARCHAR(30) NOT NULL,
    target_id    INTEGER NOT NULL,
    target_no    VARCHAR(100),
    amount       NUMERIC(15, 2) NOT NULL,
    notes        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT payment_allocations_target_type_check
        CHECK (target_type IN ('contract', 'purchase_order'))
);

COMMENT ON TABLE business.payment_allocations IS '收付款分配到合同或采购订单';
COMMENT ON COLUMN business.payment_allocations.target_type IS 'contract → contracts.id；purchase_order → purchase_orders.id';

CREATE INDEX IF NOT EXISTS idx_payment_allocations_payment_id ON business.payment_allocations (payment_id);
CREATE INDEX IF NOT EXISTS idx_payment_allocations_target ON business.payment_allocations (target_type, target_id);

-- =============================================================================
-- 四、business.inventory（按产品+色号+批次）
-- =============================================================================
CREATE TABLE business.inventory (
    id           SERIAL PRIMARY KEY,
    product_id   INTEGER NOT NULL REFERENCES business.products (id) ON DELETE RESTRICT,
    product_name   VARCHAR(500),
    color          VARCHAR(200),
    spec           VARCHAR(200),
    batch_no       VARCHAR(100),
    rn_id          INTEGER REFERENCES business.receiving_notes (id) ON DELETE SET NULL,
    rn_item_id     INTEGER REFERENCES business.receiving_note_items (id) ON DELETE SET NULL,
    warehouse      VARCHAR(200),
    initial_qty    NUMERIC(15, 2) NOT NULL,
    current_qty    NUMERIC(15, 2) NOT NULL,
    unit           VARCHAR(50),
    unit_cost      NUMERIC(15, 4),
    status         VARCHAR(20) NOT NULL DEFAULT 'in_stock',
    notes          TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT inventory_status_check
        CHECK (status IN ('in_stock', 'depleted', 'reserved', 'frozen'))
);

COMMENT ON TABLE business.inventory IS '库存批次（入：到货确认；出：发货扣减 current_qty）';
COMMENT ON COLUMN business.inventory.batch_no IS '批次号，建议与到货单 rn_no 或业务规则一致';

CREATE INDEX IF NOT EXISTS idx_inventory_product_id ON business.inventory (product_id);
CREATE INDEX IF NOT EXISTS idx_inventory_warehouse ON business.inventory (warehouse);
CREATE INDEX IF NOT EXISTS idx_inventory_rn_id ON business.inventory (rn_id);

-- =============================================================================
-- 五、shipping_note_items：出库批次
-- =============================================================================
ALTER TABLE business.shipping_note_items
    ADD COLUMN IF NOT EXISTS inventory_id INTEGER REFERENCES business.inventory (id) ON DELETE SET NULL;

COMMENT ON COLUMN business.shipping_note_items.inventory_id IS '本行发货所扣减的库存批次';

CREATE INDEX IF NOT EXISTS idx_shipping_note_items_inventory_id ON business.shipping_note_items (inventory_id);

-- =============================================================================
-- 六、contracts：已收款
-- =============================================================================
ALTER TABLE business.contracts
    ADD COLUMN IF NOT EXISTS received_amount NUMERIC(15, 2) NOT NULL DEFAULT 0;

COMMENT ON COLUMN business.contracts.received_amount IS '已收款金额（与 payment_allocations 手工对齐；无触发器）';

COMMIT;

-- =============================================================================
-- 七、元数据同步 + 领域模型开关
-- =============================================================================
CALL meta.sync_constitution();

UPDATE meta.meta_tables
SET include_in_domain_model = true
WHERE table_name IN ('payments', 'payment_allocations', 'inventory');

-- =============================================================================
-- 八、导航（系统运行：收付款父子 + 库存）
-- =============================================================================
UPDATE meta.meta_tables SET
    table_cn = COALESCE(NULLIF(TRIM(table_cn), ''), '收付款'),
    nav_section = 'run',
    nav_order = 40,
    nav_parent_table = NULL
WHERE table_name = 'payments';

UPDATE meta.meta_tables SET
    table_cn = COALESCE(NULLIF(TRIM(table_cn), ''), '付款分配'),
    nav_section = 'run',
    nav_order = 41,
    nav_parent_table = 'payments'
WHERE table_name = 'payment_allocations';

UPDATE meta.meta_tables SET
    table_cn = COALESCE(NULLIF(TRIM(table_cn), ''), '库存批次'),
    nav_section = 'run',
    nav_order = 45,
    nav_parent_table = NULL
WHERE table_name = 'inventory';
