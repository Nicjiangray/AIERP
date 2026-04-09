-- delete_contract 意图（执行仍复用 delete_entity）
-- 执行：psql -d aierp -f constitution/add_delete_contract.sql
-- 完成后：python scripts/sync_intent_embeddings.py

INSERT INTO business.workflows (intent, display_name, description, example_queries)
VALUES (
    'delete_contract',
    '删除合同',
    '软删除整份合同（主表+明细），须在 delete_entity 的 explanation 中说明删除原因',
    '[
        "删除合同",
        "作废合同",
        "取消合同",
        "把这个合同删了",
        "这份合同不要了",
        "合同作废",
        "废掉这个合同",
        "撤销合同",
        "合同取消掉",
        "这单不做了"
    ]'::jsonb
)
ON CONFLICT (intent) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    description = EXCLUDED.description,
    example_queries = EXCLUDED.example_queries;
