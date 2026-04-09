-- 合同变更证据链表
-- 执行：psql -d aierp -f constitution/add_contract_changes.sql
-- 完成后：CALL meta.sync_constitution();  python scripts/sync_meta_cn.py

CREATE TABLE IF NOT EXISTS business.contract_changes (
    id              SERIAL PRIMARY KEY,
    contract_id     INTEGER NOT NULL REFERENCES business.contracts(id),
    change_type     VARCHAR(30) NOT NULL,
    changed_by      VARCHAR(100) NOT NULL DEFAULT 'system',
    changed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    reason          TEXT NOT NULL,
    evidence_refs   JSONB NOT NULL DEFAULT '[]'::jsonb,
    snapshot_before JSONB NOT NULL,
    snapshot_after  JSONB NOT NULL,
    diff_summary    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_contract_changes_contract_id
    ON business.contract_changes(contract_id);

COMMENT ON TABLE business.contract_changes IS '合同变更证据链：每次修改/删除的前后快照、原因与结构化摘要';
