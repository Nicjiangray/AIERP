-- 到货单 + 发货单 + contract_items.shipped_qty
-- 执行：psql -U nicjiang -d aierp -f constitution/v11_receiving_shipping_tables.sql
--
-- 【本文件故意不含】CALL meta.sync_constitution()、meta.meta_tables、导航；与后续建表指令一并收尾时再跑。

BEGIN;

-- ---------------------------------------------------------------------------
-- 一、business.receiving_notes（到货单/入库单）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS business.receiving_notes (
    id                 SERIAL PRIMARY KEY,
    rn_no              VARCHAR(50)  NOT NULL UNIQUE,
    po_id              INTEGER REFERENCES business.purchase_orders (id) ON DELETE SET NULL,
    supplier_id        INTEGER REFERENCES business.suppliers (id) ON DELETE SET NULL,
    supplier_name      VARCHAR(500),
    receive_date       DATE         NOT NULL,
    warehouse          VARCHAR(200),
    status             VARCHAR(30)  NOT NULL DEFAULT 'draft',
    inspector          VARCHAR(200),
    quality_notes      TEXT,
    logistics_company  VARCHAR(200),
    logistics_no       VARCHAR(100),
    notes              TEXT,
    created_by         INTEGER REFERENCES business.employees (id) ON DELETE SET NULL,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE business.receiving_notes IS '到货单/入库单';
COMMENT ON COLUMN business.receiving_notes.rn_no IS '到货单号，如 RN-202604-001';
COMMENT ON COLUMN business.receiving_notes.status IS 'draft / confirmed / rejected';

CREATE INDEX IF NOT EXISTS idx_receiving_notes_po_id ON business.receiving_notes (po_id);
CREATE INDEX IF NOT EXISTS idx_receiving_notes_supplier_id ON business.receiving_notes (supplier_id);
CREATE INDEX IF NOT EXISTS idx_receiving_notes_receive_date ON business.receiving_notes (receive_date);

-- ---------------------------------------------------------------------------
-- 二、business.receiving_note_items（到货明细）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS business.receiving_note_items (
    id            SERIAL PRIMARY KEY,
    rn_id         INTEGER NOT NULL REFERENCES business.receiving_notes (id) ON DELETE CASCADE,
    po_item_id    INTEGER REFERENCES business.purchase_order_items (id) ON DELETE SET NULL,
    product_id    INTEGER REFERENCES business.products (id) ON DELETE SET NULL,
    product_name  VARCHAR(500),
    color         VARCHAR(200),
    spec          VARCHAR(200),
    unit          VARCHAR(50),
    expected_qty  NUMERIC(15, 2),
    actual_qty    NUMERIC(15, 2) NOT NULL,
    rejected_qty  NUMERIC(15, 2) NOT NULL DEFAULT 0,
    accepted_qty  NUMERIC(15, 2) GENERATED ALWAYS AS (actual_qty - rejected_qty) STORED,
    notes         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE business.receiving_note_items IS '到货明细';
COMMENT ON COLUMN business.receiving_note_items.accepted_qty IS '合格入库数量 = actual_qty - rejected_qty（生成列）';

CREATE INDEX IF NOT EXISTS idx_receiving_note_items_rn_id ON business.receiving_note_items (rn_id);
CREATE INDEX IF NOT EXISTS idx_receiving_note_items_po_item_id ON business.receiving_note_items (po_item_id);

-- ---------------------------------------------------------------------------
-- 三、business.shipping_notes（发货单/出库单）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS business.shipping_notes (
    id                 SERIAL PRIMARY KEY,
    sn_no              VARCHAR(50)  NOT NULL UNIQUE,
    contract_id        INTEGER REFERENCES business.contracts (id) ON DELETE SET NULL,
    customer_id        INTEGER REFERENCES business.customers (id) ON DELETE SET NULL,
    customer_name      VARCHAR(500),
    ship_date          DATE         NOT NULL,
    delivery_address   VARCHAR(500),
    status             VARCHAR(30)  NOT NULL DEFAULT 'draft',
    shipping_method    VARCHAR(100),
    logistics_company  VARCHAR(200),
    logistics_no       VARCHAR(100),
    notes              TEXT,
    created_by         INTEGER REFERENCES business.employees (id) ON DELETE SET NULL,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE business.shipping_notes IS '发货单/出库单';
COMMENT ON COLUMN business.shipping_notes.sn_no IS '发货单号，如 SN-202604-001';
COMMENT ON COLUMN business.shipping_notes.status IS 'draft / shipped / delivered / returned';

CREATE INDEX IF NOT EXISTS idx_shipping_notes_contract_id ON business.shipping_notes (contract_id);
CREATE INDEX IF NOT EXISTS idx_shipping_notes_customer_id ON business.shipping_notes (customer_id);
CREATE INDEX IF NOT EXISTS idx_shipping_notes_ship_date ON business.shipping_notes (ship_date);

-- ---------------------------------------------------------------------------
-- 四、business.shipping_note_items（发货明细）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS business.shipping_note_items (
    id               SERIAL PRIMARY KEY,
    sn_id            INTEGER NOT NULL REFERENCES business.shipping_notes (id) ON DELETE CASCADE,
    contract_item_id INTEGER REFERENCES business.contract_items (id) ON DELETE SET NULL,
    product_id       INTEGER REFERENCES business.products (id) ON DELETE SET NULL,
    product_name     VARCHAR(500),
    color            VARCHAR(200),
    spec             VARCHAR(200),
    unit             VARCHAR(50),
    quantity         NUMERIC(15, 2) NOT NULL,
    notes            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE business.shipping_note_items IS '发货明细';

CREATE INDEX IF NOT EXISTS idx_shipping_note_items_sn_id ON business.shipping_note_items (sn_id);
CREATE INDEX IF NOT EXISTS idx_shipping_note_items_contract_item_id ON business.shipping_note_items (contract_item_id);

-- ---------------------------------------------------------------------------
-- 五、contract_items：已发货数量（与 purchase_order_items.delivered_qty 对称）
-- ---------------------------------------------------------------------------
ALTER TABLE business.contract_items
    ADD COLUMN IF NOT EXISTS shipped_qty NUMERIC(15, 2) NOT NULL DEFAULT 0;

COMMENT ON COLUMN business.contract_items.shipped_qty IS '已发货数量（手工或后续流程更新；与 delivered_qty 对称）';

COMMIT;
