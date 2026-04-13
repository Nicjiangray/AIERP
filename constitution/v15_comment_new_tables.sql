-- V6 新建业务表：补全 PostgreSQL 表/列中文注释（供 sync_meta_cn 与领域模型使用）
-- 执行：psql -U nicjiang -d aierp -f constitution/v15_comment_new_tables.sql
-- 完成后：python scripts/sync_meta_pipeline.py（或至少 sync_meta_cn + sync_schema_shadow）

BEGIN;

-- =============================================================================
-- 1. suppliers
-- =============================================================================
COMMENT ON TABLE business.suppliers IS '供应商信息，包括织厂、染厂、贸易商等';

COMMENT ON COLUMN business.suppliers.id IS '主键';
COMMENT ON COLUMN business.suppliers.supplier_code IS '供应商编号';
COMMENT ON COLUMN business.suppliers.name IS '公司全称';
COMMENT ON COLUMN business.suppliers.short_name IS '简称';
COMMENT ON COLUMN business.suppliers.country IS '国家';
COMMENT ON COLUMN business.suppliers.province IS '省/州';
COMMENT ON COLUMN business.suppliers.city IS '城市';
COMMENT ON COLUMN business.suppliers.district IS '区/县';
COMMENT ON COLUMN business.suppliers.address IS '详细地址';
COMMENT ON COLUMN business.suppliers.currency IS '默认结算币种';
COMMENT ON COLUMN business.suppliers.status IS '状态（active/inactive/blacklisted 等）';
COMMENT ON COLUMN business.suppliers.payment_terms IS '付款条件（如月结30天）';
COMMENT ON COLUMN business.suppliers.tax_id IS '税号';
COMMENT ON COLUMN business.suppliers.invoice_type IS '发票类型（如增值税专用发票、普通发票）';
COMMENT ON COLUMN business.suppliers.bank_name IS '开户行';
COMMENT ON COLUMN business.suppliers.bank_account IS '银行账号';
COMMENT ON COLUMN business.suppliers.category IS '供应商类别（织厂/染厂/贸易商）';
COMMENT ON COLUMN business.suppliers.source_event IS '来源渠道（展会/转介绍/1688等）';
COMMENT ON COLUMN business.suppliers.notes IS '备注';
COMMENT ON COLUMN business.suppliers.deleted_at IS '软删除时间';
COMMENT ON COLUMN business.suppliers.created_at IS '创建时间';
COMMENT ON COLUMN business.suppliers.updated_at IS '更新时间';

-- =============================================================================
-- 2. purchase_orders
-- =============================================================================
COMMENT ON TABLE business.purchase_orders IS '采购订单/采购合同，关联供应商';

COMMENT ON COLUMN business.purchase_orders.id IS '主键';
COMMENT ON COLUMN business.purchase_orders.po_no IS '采购订单号';
COMMENT ON COLUMN business.purchase_orders.supplier_id IS '供应商ID';
COMMENT ON COLUMN business.purchase_orders.supplier_name IS '供应商名称（冗余）';
COMMENT ON COLUMN business.purchase_orders.status IS '订单状态';
COMMENT ON COLUMN business.purchase_orders.order_date IS '下单日期';
COMMENT ON COLUMN business.purchase_orders.currency IS '币种';
COMMENT ON COLUMN business.purchase_orders.total_amount IS '订单总金额';
COMMENT ON COLUMN business.purchase_orders.paid_amount IS '已付金额';
COMMENT ON COLUMN business.purchase_orders.payment_terms IS '付款条件';
COMMENT ON COLUMN business.purchase_orders.delivery_address IS '交货地址/收货仓库';
COMMENT ON COLUMN business.purchase_orders.notes IS '备注';
COMMENT ON COLUMN business.purchase_orders.created_by IS '创建人（员工ID）';
COMMENT ON COLUMN business.purchase_orders.deleted_at IS '软删除时间';
COMMENT ON COLUMN business.purchase_orders.created_at IS '创建时间';
COMMENT ON COLUMN business.purchase_orders.updated_at IS '更新时间';

-- =============================================================================
-- 3. purchase_order_items
-- =============================================================================
COMMENT ON TABLE business.purchase_order_items IS '采购订单的产品明细行';

COMMENT ON COLUMN business.purchase_order_items.id IS '主键';
COMMENT ON COLUMN business.purchase_order_items.po_id IS '所属采购订单ID';
COMMENT ON COLUMN business.purchase_order_items.product_id IS '产品ID';
COMMENT ON COLUMN business.purchase_order_items.product_code IS '产品编号（冗余）';
COMMENT ON COLUMN business.purchase_order_items.product_name IS '产品名称（冗余）';
COMMENT ON COLUMN business.purchase_order_items.color IS '颜色/色号';
COMMENT ON COLUMN business.purchase_order_items.spec IS '规格（如经纬密度）';
COMMENT ON COLUMN business.purchase_order_items.unit IS '计量单位';
COMMENT ON COLUMN business.purchase_order_items.quantity IS '采购数量';
COMMENT ON COLUMN business.purchase_order_items.unit_price IS '采购单价';
COMMENT ON COLUMN business.purchase_order_items.amount IS '行金额';
COMMENT ON COLUMN business.purchase_order_items.delivered_qty IS '已到货数量';
COMMENT ON COLUMN business.purchase_order_items.source_contract_item_id IS '关联的销售合同明细ID（这条采购是为哪个销售订单采购的）';
COMMENT ON COLUMN business.purchase_order_items.expected_delivery_date IS '该产品的预计交货日期';
COMMENT ON COLUMN business.purchase_order_items.notes IS '备注';
COMMENT ON COLUMN business.purchase_order_items.deleted_at IS '软删除时间';
COMMENT ON COLUMN business.purchase_order_items.created_at IS '创建时间';
COMMENT ON COLUMN business.purchase_order_items.updated_at IS '更新时间';

-- =============================================================================
-- 4. receiving_notes
-- =============================================================================
COMMENT ON TABLE business.receiving_notes IS '供应商到货/入库记录';

COMMENT ON COLUMN business.receiving_notes.id IS '主键';
COMMENT ON COLUMN business.receiving_notes.rn_no IS '到货单号';
COMMENT ON COLUMN business.receiving_notes.po_id IS '关联的采购订单ID';
COMMENT ON COLUMN business.receiving_notes.supplier_id IS '供应商ID（冗余）';
COMMENT ON COLUMN business.receiving_notes.supplier_name IS '供应商名称（冗余）';
COMMENT ON COLUMN business.receiving_notes.receive_date IS '实际到货日期';
COMMENT ON COLUMN business.receiving_notes.warehouse IS '收货仓库/地点';
COMMENT ON COLUMN business.receiving_notes.status IS '状态';
COMMENT ON COLUMN business.receiving_notes.inspector IS '验货人';
COMMENT ON COLUMN business.receiving_notes.quality_notes IS '验货备注（色差、瑕疵等）';
COMMENT ON COLUMN business.receiving_notes.logistics_company IS '物流公司';
COMMENT ON COLUMN business.receiving_notes.logistics_no IS '物流单号/运单号';
COMMENT ON COLUMN business.receiving_notes.notes IS '备注';
COMMENT ON COLUMN business.receiving_notes.created_by IS '创建人（员工ID）';
COMMENT ON COLUMN business.receiving_notes.created_at IS '创建时间';
COMMENT ON COLUMN business.receiving_notes.updated_at IS '更新时间';

-- =============================================================================
-- 5. receiving_note_items
-- =============================================================================
COMMENT ON TABLE business.receiving_note_items IS '到货单的产品明细行，记录每个产品的实际到货和验货情况';

COMMENT ON COLUMN business.receiving_note_items.id IS '主键';
COMMENT ON COLUMN business.receiving_note_items.rn_id IS '所属到货单ID';
COMMENT ON COLUMN business.receiving_note_items.po_item_id IS '关联的采购明细行ID';
COMMENT ON COLUMN business.receiving_note_items.product_id IS '产品ID';
COMMENT ON COLUMN business.receiving_note_items.product_name IS '产品名称（冗余）';
COMMENT ON COLUMN business.receiving_note_items.color IS '颜色/色号';
COMMENT ON COLUMN business.receiving_note_items.spec IS '规格';
COMMENT ON COLUMN business.receiving_note_items.unit IS '单位';
COMMENT ON COLUMN business.receiving_note_items.expected_qty IS '应到数量';
COMMENT ON COLUMN business.receiving_note_items.actual_qty IS '实际到货数量';
COMMENT ON COLUMN business.receiving_note_items.rejected_qty IS '不合格数量';
COMMENT ON COLUMN business.receiving_note_items.accepted_qty IS '合格入库数量（生成列：实际−不合格）';
COMMENT ON COLUMN business.receiving_note_items.notes IS '备注';
COMMENT ON COLUMN business.receiving_note_items.created_at IS '创建时间';
COMMENT ON COLUMN business.receiving_note_items.updated_at IS '更新时间';

-- =============================================================================
-- 6. shipping_notes
-- =============================================================================
COMMENT ON TABLE business.shipping_notes IS '客户发货/出库记录';

COMMENT ON COLUMN business.shipping_notes.id IS '主键';
COMMENT ON COLUMN business.shipping_notes.sn_no IS '发货单号';
COMMENT ON COLUMN business.shipping_notes.contract_id IS '关联的销售合同ID';
COMMENT ON COLUMN business.shipping_notes.customer_id IS '客户ID（冗余）';
COMMENT ON COLUMN business.shipping_notes.customer_name IS '客户名称（冗余）';
COMMENT ON COLUMN business.shipping_notes.ship_date IS '发货日期';
COMMENT ON COLUMN business.shipping_notes.delivery_address IS '收货地址';
COMMENT ON COLUMN business.shipping_notes.status IS '状态';
COMMENT ON COLUMN business.shipping_notes.shipping_method IS '运输方式（快递/物流/自提/海运）';
COMMENT ON COLUMN business.shipping_notes.logistics_company IS '物流公司';
COMMENT ON COLUMN business.shipping_notes.logistics_no IS '物流单号/运单号';
COMMENT ON COLUMN business.shipping_notes.notes IS '备注';
COMMENT ON COLUMN business.shipping_notes.created_by IS '创建人（员工ID）';
COMMENT ON COLUMN business.shipping_notes.created_at IS '创建时间';
COMMENT ON COLUMN business.shipping_notes.updated_at IS '更新时间';

-- =============================================================================
-- 7. shipping_note_items
-- =============================================================================
COMMENT ON TABLE business.shipping_note_items IS '发货单的产品明细行';

COMMENT ON COLUMN business.shipping_note_items.id IS '主键';
COMMENT ON COLUMN business.shipping_note_items.sn_id IS '所属发货单ID';
COMMENT ON COLUMN business.shipping_note_items.contract_item_id IS '关联的销售合同明细行ID';
COMMENT ON COLUMN business.shipping_note_items.product_id IS '产品ID';
COMMENT ON COLUMN business.shipping_note_items.product_name IS '产品名称（冗余）';
COMMENT ON COLUMN business.shipping_note_items.color IS '颜色/色号';
COMMENT ON COLUMN business.shipping_note_items.spec IS '规格';
COMMENT ON COLUMN business.shipping_note_items.unit IS '单位';
COMMENT ON COLUMN business.shipping_note_items.quantity IS '发货数量';
COMMENT ON COLUMN business.shipping_note_items.inventory_id IS '从哪个库存批次出货';
COMMENT ON COLUMN business.shipping_note_items.notes IS '备注';
COMMENT ON COLUMN business.shipping_note_items.created_at IS '创建时间';
COMMENT ON COLUMN business.shipping_note_items.updated_at IS '更新时间';

-- =============================================================================
-- 8. payments
-- =============================================================================
COMMENT ON TABLE business.payments IS '所有收款和付款的流水记录';

COMMENT ON COLUMN business.payments.id IS '主键';
COMMENT ON COLUMN business.payments.payment_no IS '收付款流水号';
COMMENT ON COLUMN business.payments.direction IS '方向（in=收款，out=付款）';
COMMENT ON COLUMN business.payments.counterparty_type IS '对方类型（customer=客户，supplier=供应商）';
COMMENT ON COLUMN business.payments.counterparty_id IS '对方ID（客户ID或供应商ID）';
COMMENT ON COLUMN business.payments.counterparty_name IS '对方名称（冗余）';
COMMENT ON COLUMN business.payments.amount IS '本次收/付金额';
COMMENT ON COLUMN business.payments.currency IS '币种';
COMMENT ON COLUMN business.payments.payment_date IS '收/付款日期';
COMMENT ON COLUMN business.payments.payment_method IS '付款方式（银行转账/承兑汇票/信用证/现金等）';
COMMENT ON COLUMN business.payments.bank_ref IS '银行流水号/参考号';
COMMENT ON COLUMN business.payments.payment_type IS '款项类型（deposit=定金，progress=进度款，final=尾款，monthly_settlement=月结，refund=退款）';
COMMENT ON COLUMN business.payments.exchange_rate IS '汇率（外贸场景）';
COMMENT ON COLUMN business.payments.amount_local IS '本币金额（外贸换算后的人民币金额）';
COMMENT ON COLUMN business.payments.notes IS '备注';
COMMENT ON COLUMN business.payments.created_by IS '创建人（员工ID）';
COMMENT ON COLUMN business.payments.created_at IS '创建时间';
COMMENT ON COLUMN business.payments.updated_at IS '更新时间';

-- =============================================================================
-- 9. payment_allocations
-- =============================================================================
COMMENT ON TABLE business.payment_allocations IS '将一笔收付款分配到具体的合同或采购订单，支持一笔付款对应多个合同的月结场景';

COMMENT ON COLUMN business.payment_allocations.id IS '主键';
COMMENT ON COLUMN business.payment_allocations.payment_id IS '所属收付款记录ID';
COMMENT ON COLUMN business.payment_allocations.target_type IS '分配目标类型（contract=销售合同，purchase_order=采购订单）';
COMMENT ON COLUMN business.payment_allocations.target_id IS '目标ID（合同ID或采购订单ID）';
COMMENT ON COLUMN business.payment_allocations.target_no IS '目标单号（冗余，如 CT-202604-052）';
COMMENT ON COLUMN business.payment_allocations.amount IS '分配到此目标的金额';
COMMENT ON COLUMN business.payment_allocations.notes IS '备注';
COMMENT ON COLUMN business.payment_allocations.created_at IS '创建时间';

-- =============================================================================
-- 10. inventory
-- =============================================================================
COMMENT ON TABLE business.inventory IS '按批次管理的库存记录，每条对应一次到货入库';

COMMENT ON COLUMN business.inventory.id IS '主键';
COMMENT ON COLUMN business.inventory.product_id IS '产品ID';
COMMENT ON COLUMN business.inventory.product_name IS '产品名称（冗余）';
COMMENT ON COLUMN business.inventory.color IS '颜色/色号';
COMMENT ON COLUMN business.inventory.spec IS '规格';
COMMENT ON COLUMN business.inventory.batch_no IS '批次号';
COMMENT ON COLUMN business.inventory.rn_id IS '关联的到货单ID（这批货从哪次到货来的）';
COMMENT ON COLUMN business.inventory.rn_item_id IS '关联的到货明细行ID';
COMMENT ON COLUMN business.inventory.warehouse IS '存放仓库/位置';
COMMENT ON COLUMN business.inventory.initial_qty IS '入库数量';
COMMENT ON COLUMN business.inventory.current_qty IS '当前库存数量（发货后扣减）';
COMMENT ON COLUMN business.inventory.unit IS '单位';
COMMENT ON COLUMN business.inventory.unit_cost IS '入库单价（采购单价，用于成本核算）';
COMMENT ON COLUMN business.inventory.status IS '状态（in_stock=有货，depleted=已清，reserved=已预留，frozen=冻结）';
COMMENT ON COLUMN business.inventory.notes IS '备注';
COMMENT ON COLUMN business.inventory.created_at IS '创建时间';
COMMENT ON COLUMN business.inventory.updated_at IS '更新时间';

-- =============================================================================
-- 已有表新增列（补充/统一措辞）
-- =============================================================================
COMMENT ON COLUMN business.contract_items.shipped_qty IS '已发货数量';
COMMENT ON COLUMN business.contract_items.expected_delivery_date IS '该产品的预计交货日期';

COMMENT ON COLUMN business.contracts.received_amount IS '已收款金额';

COMMIT;

CALL meta.sync_constitution();
