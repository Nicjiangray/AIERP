/**
 * 主表 + 子表联动：路由表名为父或子时均进入同一分屏页。
 * parent.key：主表上用于过滤子表的外键取值列。
 * child.fk：子表上的列名。
 */
export const TABLE_SPLIT = {
  contracts: {
    parent: { schema: 'business', table: 'contracts', key: 'id' },
    child: { schema: 'business', table: 'contract_items', fk: 'contract_id' },
  },
  contract_items: {
    parent: { schema: 'business', table: 'contracts', key: 'id' },
    child: { schema: 'business', table: 'contract_items', fk: 'contract_id' },
  },
  purchase_orders: {
    parent: { schema: 'business', table: 'purchase_orders', key: 'id' },
    child: { schema: 'business', table: 'purchase_order_items', fk: 'po_id' },
  },
  purchase_order_items: {
    parent: { schema: 'business', table: 'purchase_orders', key: 'id' },
    child: { schema: 'business', table: 'purchase_order_items', fk: 'po_id' },
  },
  payments: {
    parent: { schema: 'business', table: 'payments', key: 'id' },
    child: { schema: 'business', table: 'payment_allocations', fk: 'payment_id' },
  },
  payment_allocations: {
    parent: { schema: 'business', table: 'payments', key: 'id' },
    child: { schema: 'business', table: 'payment_allocations', fk: 'payment_id' },
  },
}

export function splitConfigForRouteTable(tableName) {
  if (!tableName) return null
  return TABLE_SPLIT[tableName] ?? null
}

/** 导航高亮：当前路由表是否属于该 split 组 */
export function navItemInSplitGroup(routeTable, itemTableName) {
  const cfg = splitConfigForRouteTable(routeTable)
  if (!cfg) return false
  return (
    itemTableName === cfg.parent.table || itemTableName === cfg.child.table
  )
}
