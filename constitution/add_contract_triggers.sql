-- 合同明细金额与主表总额：由触发器计算（V15：应用层不再汇总）
-- 执行：psql -d aierp -f constitution/add_contract_triggers.sql
-- 依赖：business.contract_items.amount、business.contracts.total_amount 列已存在

CREATE OR REPLACE FUNCTION business.trg_calc_item_amount()
RETURNS TRIGGER AS $$
BEGIN
  NEW.amount := COALESCE(NEW.quantity, 0) * COALESCE(NEW.unit_price, 0);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_contract_items_calc_amount ON business.contract_items;
CREATE TRIGGER trg_contract_items_calc_amount
  BEFORE INSERT OR UPDATE ON business.contract_items
  FOR EACH ROW EXECUTE FUNCTION business.trg_calc_item_amount();

CREATE OR REPLACE FUNCTION business.trg_sync_contract_total()
RETURNS TRIGGER AS $$
DECLARE
  v_contract_id INTEGER;
BEGIN
  v_contract_id := COALESCE(NEW.contract_id, OLD.contract_id);
  UPDATE business.contracts
     SET total_amount = (
           SELECT COALESCE(SUM(amount), 0)
             FROM business.contract_items
            WHERE contract_id = v_contract_id
              AND deleted_at IS NULL
         ),
         updated_at = now()
   WHERE id = v_contract_id;
  RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_contract_items_sync_total ON business.contract_items;
CREATE TRIGGER trg_contract_items_sync_total
  AFTER INSERT OR UPDATE OR DELETE ON business.contract_items
  FOR EACH ROW EXECUTE FUNCTION business.trg_sync_contract_total();
