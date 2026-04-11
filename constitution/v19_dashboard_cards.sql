-- 仪表盘卡片配置表 + 角色默认种子 + 提示词补充
-- psql -d aierp -f constitution/v19_dashboard_cards.sql

BEGIN;

CREATE TABLE IF NOT EXISTS business.dashboard_cards (
    id SERIAL PRIMARY KEY,
    employee_id INT REFERENCES business.employees(id),
    role_code VARCHAR(30),
    title VARCHAR(100) NOT NULL,
    card_type VARCHAR(20) NOT NULL DEFAULT 'number'
        CHECK (card_type IN ('number', 'bar', 'pie', 'line', 'list')),
    query_sql TEXT NOT NULL,
    description TEXT,
    display_order INT NOT NULL DEFAULT 0,
    card_config JSONB,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_by INT REFERENCES business.employees(id),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    deleted_at TIMESTAMPTZ,
    CHECK (employee_id IS NOT NULL OR role_code IS NOT NULL)
);

COMMENT ON TABLE business.dashboard_cards IS '仪表盘卡片配置：每张卡片一条 SELECT SQL，由 LLM 配置，前端经 execute_sql 审计执行';
COMMENT ON COLUMN business.dashboard_cards.employee_id IS '个人定制；NULL 表示角色默认';
COMMENT ON COLUMN business.dashboard_cards.role_code IS '角色默认：admin/sales/warehouse/finance 等';
COMMENT ON COLUMN business.dashboard_cards.card_type IS 'number/bar/pie/line/list，见项目文档列名约定';
COMMENT ON COLUMN business.dashboard_cards.query_sql IS '仅 SELECT；可用 :user_id 占位符';

CREATE INDEX IF NOT EXISTS idx_dashboard_cards_employee
    ON business.dashboard_cards(employee_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_dashboard_cards_role
    ON business.dashboard_cards(role_code) WHERE deleted_at IS NULL;

-- admin 默认
INSERT INTO business.dashboard_cards (role_code, title, card_type, query_sql, display_order, card_config) VALUES
('admin', '本月新签合同', 'number',
 'SELECT COUNT(*)::bigint AS value FROM business.contracts WHERE signed_date >= date_trunc(''month'', CURRENT_DATE)::date AND deleted_at IS NULL',
 1, '{"unit":"单","color":"#1976D2"}'::jsonb),
('admin', '本月采购订单', 'number',
 'SELECT COUNT(*)::bigint AS value FROM business.purchase_orders WHERE order_date >= date_trunc(''month'', CURRENT_DATE)::date AND deleted_at IS NULL',
 2, '{"unit":"单","color":"#FF9800"}'::jsonb),
('admin', '本月收款总额', 'number',
 'SELECT COALESCE(SUM(amount), 0) AS value FROM business.payments WHERE direction = ''in'' AND payment_date >= date_trunc(''month'', CURRENT_DATE)::date',
 3, '{"unit":"元","color":"#4CAF50","prefix":"¥"}'::jsonb),
('admin', '本月付款总额', 'number',
 'SELECT COALESCE(SUM(amount), 0) AS value FROM business.payments WHERE direction = ''out'' AND payment_date >= date_trunc(''month'', CURRENT_DATE)::date',
 4, '{"unit":"元","color":"#F44336","prefix":"¥"}'::jsonb),
('admin', '待审批单据', 'list',
 'SELECT a.approval_no, a.target_type::text, a.target_no::text, e.name AS requester, a.requested_at::date AS request_date FROM business.approvals a JOIN business.employees e ON e.id = a.requested_by WHERE a.status = ''pending'' AND a.deleted_at IS NULL ORDER BY a.requested_at DESC LIMIT 10',
 5, NULL);

-- sales
INSERT INTO business.dashboard_cards (role_code, title, card_type, query_sql, display_order, card_config) VALUES
('sales', '我的本月签约', 'number',
 'SELECT COUNT(*)::bigint AS value FROM business.contracts WHERE signed_date >= date_trunc(''month'', CURRENT_DATE)::date AND owner_id = :user_id AND deleted_at IS NULL',
 1, '{"unit":"单","color":"#1976D2"}'::jsonb),
('sales', '我的待发货合同', 'number',
 'SELECT COUNT(*)::bigint AS value FROM business.contracts c WHERE c.status = ''active'' AND c.deleted_at IS NULL AND c.owner_id = :user_id AND EXISTS (SELECT 1 FROM business.contract_items ci WHERE ci.contract_id = c.id AND ci.quantity > COALESCE(ci.shipped_qty, 0))',
 2, '{"unit":"单","color":"#FF9800"}'::jsonb),
('sales', '我的本月签约金额', 'number',
 'SELECT COALESCE(SUM(total_amount), 0) AS value FROM business.contracts WHERE signed_date >= date_trunc(''month'', CURRENT_DATE)::date AND owner_id = :user_id AND deleted_at IS NULL',
 3, '{"unit":"元","color":"#4CAF50","prefix":"¥"}'::jsonb);

-- warehouse
INSERT INTO business.dashboard_cards (role_code, title, card_type, query_sql, display_order, card_config) VALUES
('warehouse', '待验货到货单', 'number',
 'SELECT COUNT(*)::bigint AS value FROM business.receiving_notes WHERE status = ''draft''',
 1, '{"unit":"单","color":"#FF9800"}'::jsonb),
('warehouse', '今日待发货', 'number',
 'SELECT COUNT(*)::bigint AS value FROM business.shipping_notes WHERE ship_date = CURRENT_DATE AND status <> ''shipped''',
 2, '{"unit":"单","color":"#F44336"}'::jsonb),
('warehouse', '库存预警（不足10）', 'list',
 'SELECT product_name, color, spec, current_qty, unit, warehouse FROM business.inventory WHERE current_qty < 10 AND current_qty > 0 AND status <> ''depleted'' ORDER BY current_qty ASC LIMIT 10',
 3, NULL);

-- finance
INSERT INTO business.dashboard_cards (role_code, title, card_type, query_sql, display_order, card_config) VALUES
('finance', '本月收款', 'number',
 'SELECT COALESCE(SUM(amount), 0) AS value FROM business.payments WHERE direction = ''in'' AND payment_date >= date_trunc(''month'', CURRENT_DATE)::date',
 1, '{"unit":"元","color":"#4CAF50","prefix":"¥"}'::jsonb),
('finance', '本月付款', 'number',
 'SELECT COALESCE(SUM(amount), 0) AS value FROM business.payments WHERE direction = ''out'' AND payment_date >= date_trunc(''month'', CURRENT_DATE)::date',
 2, '{"unit":"元","color":"#F44336","prefix":"¥"}'::jsonb),
('finance', '应收未收（已生效合同）', 'list',
 'SELECT c.contract_no, cu.name AS customer_name, c.total_amount, COALESCE(c.received_amount, 0) AS received, c.total_amount - COALESCE(c.received_amount, 0) AS outstanding FROM business.contracts c LEFT JOIN business.customers cu ON cu.id = c.customer_id WHERE c.status = ''active'' AND c.total_amount > COALESCE(c.received_amount, 0) AND c.deleted_at IS NULL ORDER BY outstanding DESC LIMIT 10',
 3, NULL);

COMMIT;

CALL meta.sync_constitution();

UPDATE meta.meta_tables
SET include_in_domain_model = true
WHERE table_name = 'dashboard_cards';

-- 提示词补充
UPDATE meta.prompts
SET
    content = content || E'\n\n'
        || $dash$ - 仪表盘配置：为指定员工或角色配置仪表盘卡片（business.dashboard_cards，新增/修改/重排）
  [TOOL]
  action: write
  intent: 为销售角色增加一张本月签约金额的数字卡片
  [/TOOL]
$dash$,
    version = version + 1,
    updated_at = NOW(),
    updated_by = 'system'
WHERE prompt_code = 'tool_write_interface'
  AND is_active = true;

UPDATE meta.prompts
SET
    content = content || E'\n\n'
        || $dex$【仪表盘卡片配置规则】

目标表：business.dashboard_cards

卡片类型与 SQL 返回格式：
- number：单行，列名必须是 value。如 SELECT COUNT(*) AS value
- bar：多行，列名 label + value
- pie：多行，列名 name + value
- line：多行，列名 date + value（或 label + value），按时间排序
- list：多行多列，列名即表头

SQL 要求：
- 只能是 SELECT
- 涉及当前用户的数据用 :user_id 占位符（执行时替换为登录员工 id）
- 软删表加 deleted_at IS NULL
- 时间范围用 date_trunc('month', CURRENT_DATE) 等相对表达式

card_config 常用：unit、color（#RRGGBB）、prefix（如 ¥）

角色默认：employee_id NULL，填 role_code。个人定制：设 employee_id，role_code NULL。

一次可插入多条，display_order 控制顺序。
$dex$,
    version = version + 1,
    updated_at = NOW(),
    updated_by = 'system'
WHERE prompt_code = 'tool_expert_executor'
  AND is_active = true;
