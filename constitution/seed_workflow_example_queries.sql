-- 在 add_workflow_intent_embeddings.sql 执行后运行：示例句入库
UPDATE business.workflows SET example_queries = '[
  "查一下客户列表",
  "看看我们有哪些客户",
  "客户有哪些",
  "查客户",
  "查一下产品",
  "看看产品列表",
  "有哪些产品",
  "查查我们的供应商",
  "合同有哪些",
  "查一下合同记录",
  "有没有XX客户",
  "XX产品有吗",
  "查一下联系人",
  "看看我们的合同"
]'::jsonb WHERE intent = 'query_master_data';

-- create_contract 的 example_queries 由 constitution/add_create_contract.sql 维护（避免同步脚本覆盖）
