-- 权限矩阵（表级）+ 审批流（合同 / 采购订单）
-- 执行：psql -d aierp -f constitution/v17_permission_and_approval.sql

BEGIN;

-- ---------------------------------------------------------------------------
-- 1) business.role_permissions
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS business.role_permissions (
    id SERIAL PRIMARY KEY,
    role_code VARCHAR(30) NOT NULL,
    table_name VARCHAR(100) NOT NULL,
    can_read BOOLEAN NOT NULL DEFAULT true,
    can_write BOOLEAN NOT NULL DEFAULT false,
    can_delete BOOLEAN NOT NULL DEFAULT false,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(role_code, table_name)
);

COMMENT ON TABLE business.role_permissions IS '角色-表级权限矩阵：定义每个角色对每张业务表的读/写/删权限';

-- ---------------------------------------------------------------------------
-- 2) business.approval_rules
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS business.approval_rules (
    id SERIAL PRIMARY KEY,
    target_type VARCHAR(30) NOT NULL,
    condition_expr TEXT,
    approver_role VARCHAR(30) NOT NULL DEFAULT 'admin',
    is_active BOOLEAN NOT NULL DEFAULT true,
    notes TEXT,
    UNIQUE (target_type, condition_expr)
);

COMMENT ON TABLE business.approval_rules IS '审批规则配置：哪些单据类型需要审批、由什么角色审批；condition_expr 空串表示全部单据';

INSERT INTO business.approval_rules (target_type, condition_expr, approver_role, notes) VALUES
('contract', '', 'admin', '所有销售合同需管理员审批'),
('purchase_order', '', 'admin', '所有采购订单需管理员审批')
ON CONFLICT (target_type, condition_expr) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 3) business.approvals
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS business.approvals (
    id SERIAL PRIMARY KEY,
    approval_no VARCHAR(30) NOT NULL UNIQUE,
    target_type VARCHAR(30) NOT NULL,
    target_id INT NOT NULL,
    target_no VARCHAR(50),
    requested_by INT NOT NULL REFERENCES business.employees (id),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    approver_id INT REFERENCES business.employees (id),
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),
    decision_at TIMESTAMPTZ,
    comment TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    deleted_at TIMESTAMPTZ
);

COMMENT ON TABLE business.approvals IS '审批记录：合同、采购订单创建后需审批通过才能进入后续流程';

CREATE INDEX IF NOT EXISTS idx_approvals_status ON business.approvals (status) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_approvals_approver ON business.approvals (approver_id, status) WHERE deleted_at IS NULL;

-- ---------------------------------------------------------------------------
-- 4) 行级归属 owner_id
-- ---------------------------------------------------------------------------
ALTER TABLE business.customers ADD COLUMN IF NOT EXISTS owner_id INT REFERENCES business.employees (id);
COMMENT ON COLUMN business.customers.owner_id IS '负责人（业务员）';

ALTER TABLE business.contracts ADD COLUMN IF NOT EXISTS owner_id INT REFERENCES business.employees (id);
COMMENT ON COLUMN business.contracts.owner_id IS '负责人（业务员）';

ALTER TABLE business.purchase_orders ADD COLUMN IF NOT EXISTS owner_id INT REFERENCES business.employees (id);
COMMENT ON COLUMN business.purchase_orders.owner_id IS '负责人';

ALTER TABLE business.shipping_notes ADD COLUMN IF NOT EXISTS owner_id INT REFERENCES business.employees (id);
COMMENT ON COLUMN business.shipping_notes.owner_id IS '负责人';

COMMENT ON COLUMN business.contracts.status IS 'draft / pending_approval / active / rejected / completed / cancelled 等';
COMMENT ON COLUMN business.purchase_orders.status IS 'draft / pending_approval / active（审批通过后的生效态，与合同一致）/ rejected / partial_delivered / completed / cancelled 等；历史或手工仍可能出现 confirmed，下游宜与 active 等同视之';

-- ---------------------------------------------------------------------------
-- 5) 角色权限种子（admin：全 business 表）
-- ---------------------------------------------------------------------------
INSERT INTO business.role_permissions (role_code, table_name, can_read, can_write, can_delete)
SELECT 'admin', schemaname || '.' || tablename, true, true, true
FROM pg_tables
WHERE schemaname = 'business'
  AND tablename NOT IN ('ai_chat', 'ai_sessions', 'ai_memory', 'user_accounts')
ON CONFLICT (role_code, table_name) DO UPDATE SET
    can_read = EXCLUDED.can_read,
    can_write = EXCLUDED.can_write,
    can_delete = EXCLUDED.can_delete,
    updated_at = now();

INSERT INTO business.role_permissions (role_code, table_name, can_read, can_write, can_delete) VALUES
('sales', 'business.customers', true, true, false),
('sales', 'business.contacts', true, true, false),
('sales', 'business.contracts', true, true, false),
('sales', 'business.contract_items', true, true, false),
('sales', 'business.shipping_notes', true, true, false),
('sales', 'business.shipping_note_items', true, true, false),
('sales', 'business.products', true, false, false),
('sales', 'business.suppliers', true, false, false),
('sales', 'business.purchase_orders', true, false, false),
('sales', 'business.purchase_order_items', true, false, false),
('sales', 'business.receiving_notes', true, false, false),
('sales', 'business.receiving_note_items', true, false, false),
('sales', 'business.inventory', true, false, false),
('sales', 'business.payments', true, false, false),
('sales', 'business.payment_allocations', true, false, false),
('sales', 'business.approvals', true, false, false),
('sales', 'business.employees', true, false, false)
ON CONFLICT (role_code, table_name) DO UPDATE SET
    can_read = EXCLUDED.can_read, can_write = EXCLUDED.can_write, can_delete = EXCLUDED.can_delete, updated_at = now();

INSERT INTO business.role_permissions (role_code, table_name, can_read, can_write, can_delete) VALUES
('warehouse', 'business.receiving_notes', true, true, false),
('warehouse', 'business.receiving_note_items', true, true, false),
('warehouse', 'business.inventory', true, true, false),
('warehouse', 'business.shipping_notes', true, true, false),
('warehouse', 'business.shipping_note_items', true, true, false),
('warehouse', 'business.products', true, false, false),
('warehouse', 'business.customers', true, false, false),
('warehouse', 'business.contracts', true, false, false),
('warehouse', 'business.contract_items', true, false, false),
('warehouse', 'business.purchase_orders', true, false, false),
('warehouse', 'business.purchase_order_items', true, false, false),
('warehouse', 'business.suppliers', true, false, false),
('warehouse', 'business.approvals', true, false, false),
('warehouse', 'business.employees', true, false, false)
ON CONFLICT (role_code, table_name) DO UPDATE SET
    can_read = EXCLUDED.can_read, can_write = EXCLUDED.can_write, can_delete = EXCLUDED.can_delete, updated_at = now();

INSERT INTO business.role_permissions (role_code, table_name, can_read, can_write, can_delete) VALUES
('finance', 'business.payments', true, true, false),
('finance', 'business.payment_allocations', true, true, false),
('finance', 'business.customers', true, false, false),
('finance', 'business.contracts', true, false, false),
('finance', 'business.contract_items', true, false, false),
('finance', 'business.purchase_orders', true, false, false),
('finance', 'business.purchase_order_items', true, false, false),
('finance', 'business.suppliers', true, false, false),
('finance', 'business.shipping_notes', true, false, false),
('finance', 'business.shipping_note_items', true, false, false),
('finance', 'business.receiving_notes', true, false, false),
('finance', 'business.receiving_note_items', true, false, false),
('finance', 'business.inventory', true, false, false),
('finance', 'business.approvals', true, false, false),
('finance', 'business.employees', true, false, false)
ON CONFLICT (role_code, table_name) DO UPDATE SET
    can_read = EXCLUDED.can_read, can_write = EXCLUDED.can_write, can_delete = EXCLUDED.can_delete, updated_at = now();

COMMIT;

CALL meta.sync_constitution();
