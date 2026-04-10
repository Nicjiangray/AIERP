-- 采购模块：suppliers + purchase_orders + purchase_order_items
-- 执行：psql -U nicjiang -d aierp -f constitution/v10_purchasing_suppliers_po.sql
-- 完成后：CALL meta.sync_constitution(); 然后 python scripts/sync_meta_pipeline.py（或至少 sync_meta_cn + sync_schema_shadow）

BEGIN;

-- ---------------------------------------------------------------------------
-- 1) business.suppliers（对齐 customers 地址与主数据风格）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS business.suppliers (
    id              SERIAL PRIMARY KEY,
    supplier_code   VARCHAR(50)  NOT NULL UNIQUE,
    name            VARCHAR(500) NOT NULL,
    short_name      VARCHAR(200),
    country         VARCHAR(100),
    province        VARCHAR(100),
    city            VARCHAR(100),
    district        VARCHAR(100),
    address         VARCHAR(500),
    currency        VARCHAR(10)  NOT NULL DEFAULT 'CNY',
    status          VARCHAR(20)  NOT NULL DEFAULT 'active',
    payment_terms   VARCHAR(500),
    tax_id          VARCHAR(100),
    invoice_type    VARCHAR(100),
    bank_name       VARCHAR(200),
    bank_account    VARCHAR(100),
    category        VARCHAR(100),
    source_event    VARCHAR(200),
    notes           TEXT,
    deleted_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE business.suppliers IS '供应商主数据；联系人见 business.contacts（company_type=supplier, company_id=suppliers.id）';
COMMENT ON COLUMN business.suppliers.supplier_code IS '供应商编号，如 SUP-001';
COMMENT ON COLUMN business.suppliers.status IS 'active / inactive / blacklisted';
COMMENT ON COLUMN business.suppliers.invoice_type IS '如：增值税专用发票、普通发票';

-- ---------------------------------------------------------------------------
-- 2) business.purchase_orders
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS business.purchase_orders (
    id                      SERIAL PRIMARY KEY,
    po_no                   VARCHAR(50)  NOT NULL UNIQUE,
    supplier_id             INTEGER REFERENCES business.suppliers (id),
    supplier_name           VARCHAR(500),
    status                  VARCHAR(30)  NOT NULL DEFAULT 'draft',
    order_date              DATE,
    expected_delivery_date  DATE,
    currency                VARCHAR(10)  NOT NULL DEFAULT 'CNY',
    total_amount            NUMERIC(15, 2) NOT NULL DEFAULT 0,
    paid_amount             NUMERIC(15, 2) NOT NULL DEFAULT 0,
    payment_terms           VARCHAR(500),
    delivery_address        VARCHAR(500),
    notes                   TEXT,
    created_by              INTEGER REFERENCES business.employees (id),
    deleted_at              TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE business.purchase_orders IS '采购订单主表';
COMMENT ON COLUMN business.purchase_orders.po_no IS '采购单号，如 PO-202604-001';
COMMENT ON COLUMN business.purchase_orders.status IS 'draft / confirmed / partial_delivered / completed / cancelled';
COMMENT ON COLUMN business.purchase_orders.paid_amount IS '已付金额（可手工维护，后续接付款模块）';

CREATE INDEX IF NOT EXISTS idx_purchase_orders_supplier_id ON business.purchase_orders (supplier_id);
CREATE INDEX IF NOT EXISTS idx_purchase_orders_order_date ON business.purchase_orders (order_date);

-- ---------------------------------------------------------------------------
-- 3) business.purchase_order_items
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS business.purchase_order_items (
    id                       SERIAL PRIMARY KEY,
    po_id                    INTEGER NOT NULL REFERENCES business.purchase_orders (id) ON DELETE CASCADE,
    product_id               INTEGER REFERENCES business.products (id),
    product_code             VARCHAR(100),
    product_name             VARCHAR(500),
    color                    VARCHAR(200),
    spec                     VARCHAR(200),
    unit                     VARCHAR(50),
    quantity                 NUMERIC(15, 2) NOT NULL,
    unit_price               NUMERIC(15, 4) NOT NULL,
    amount                   NUMERIC(15, 2),
    delivered_qty            NUMERIC(15, 2) NOT NULL DEFAULT 0,
    source_contract_item_id  INTEGER REFERENCES business.contract_items (id) ON DELETE SET NULL,
    notes                    TEXT,
    deleted_at               TIMESTAMPTZ,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE business.purchase_order_items IS '采购订单明细';
COMMENT ON COLUMN business.purchase_order_items.source_contract_item_id IS '对应销售合同明细（为本采购溯源）';

CREATE INDEX IF NOT EXISTS idx_purchase_order_items_po_id ON business.purchase_order_items (po_id);
CREATE INDEX IF NOT EXISTS idx_purchase_order_items_product_id ON business.purchase_order_items (product_id);

-- ---------------------------------------------------------------------------
-- 4) 种子：测试供应商（2～3 条）
-- ---------------------------------------------------------------------------
INSERT INTO business.suppliers (
    supplier_code, name, short_name,
    province, city, district, address,
    currency, status, category, source_event, notes
) VALUES
(
    'SUP-001',
    '广州锦绣纺织有限公司',
    '锦绣纺织',
    '广东省',
    '广州市',
    '白云区',
    '白云区纺织工业园示例路1号',
    'CNY',
    'active',
    '织厂',
    '展会',
    '测试数据'
),
(
    'SUP-002',
    '佛山顺德永兴染整厂',
    '永兴染整',
    '广东省',
    '佛山市',
    '顺德区',
    '顺德区印染集中区示例大道88号',
    'CNY',
    'active',
    '染厂',
    '转介绍',
    '测试数据'
),
(
    'SUP-003',
    '绍兴柯桥华丰布业',
    '华丰布业',
    '浙江省',
    '绍兴市',
    '柯桥区',
    '柯桥轻纺城示例街99号',
    'CNY',
    'active',
    '贸易商',
    '1688',
    '测试数据'
)
ON CONFLICT (supplier_code) DO NOTHING;

COMMIT;

-- 同步元数据目录与字段行
CALL meta.sync_constitution();

-- 确保纳入领域模型白名单（新表在 sync 中已默认 business=true；此处幂等加固）
UPDATE meta.meta_tables
SET include_in_domain_model = true
WHERE table_name IN ('suppliers', 'purchase_orders', 'purchase_order_items');
