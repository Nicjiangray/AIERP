-- ============================================================
-- AIERP 新增两张表
-- 1. business.memories  — 集中向量记忆表
-- 2. business.contracts — 合同主线表
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- 1. business.memories
-- 集中存储所有业务表的语义内容和向量
-- 原则：原表只存结构化字段，语义和向量统一在这里
-- ------------------------------------------------------------
CREATE TABLE business.memories (
    id           SERIAL PRIMARY KEY,
    source_table VARCHAR(50)  NOT NULL,
    source_id    INTEGER      NOT NULL,
    content      TEXT         NOT NULL,
    embedding    vector(768),
    created_at   TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_table, source_id)
);

COMMENT ON TABLE  business.memories              IS 'AI集中记忆表，存储所有业务表的语义内容和向量索引';
COMMENT ON COLUMN business.memories.source_table IS '来源表名，如：events / attachments / event_steps / customers';
COMMENT ON COLUMN business.memories.source_id    IS '来源表的记录id';
COMMENT ON COLUMN business.memories.content      IS '用于生成向量的文字内容，从原始记录提取';
COMMENT ON COLUMN business.memories.embedding    IS '768维语义向量，用于相似度检索';

-- ------------------------------------------------------------
-- 2. business.contracts
-- 合同主线表，串联所有相关事件
-- 轻量设计：细节在事件里，这里只是锚点
-- ------------------------------------------------------------
CREATE TABLE business.contracts (
    id                SERIAL PRIMARY KEY,
    contract_code     VARCHAR(50)  NOT NULL UNIQUE,
    contract_type     VARCHAR(20)  NOT NULL,
    title             VARCHAR(200),
    customer_id       INTEGER,
    supplier_id       INTEGER,
    status            VARCHAR(20)  NOT NULL DEFAULT 'active',
    created_from      VARCHAR(50),
    created_by        INTEGER,
    confirmed_by      INTEGER,
    created_at        TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    notes             TEXT
);

COMMENT ON TABLE  business.contracts               IS '合同主线表，是多个事件的锚点。细节不在这里，在各自的事件里';
COMMENT ON COLUMN business.contracts.contract_code IS '合同编号，唯一，如：SC-2026-001（销售）/ PC-2026-001（采购）';
COMMENT ON COLUMN business.contracts.contract_type IS 'sales销售合同 / purchase采购合同';
COMMENT ON COLUMN business.contracts.customer_id   IS '销售合同关联 meta.customers.id';
COMMENT ON COLUMN business.contracts.supplier_id   IS '采购合同关联 meta.suppliers.id';
COMMENT ON COLUMN business.contracts.status        IS 'draft草稿 / active执行中 / completed完成 / cancelled取消';
COMMENT ON COLUMN business.contracts.created_from  IS '从哪个询价事件升级来的，关联business.events.event_code';
COMMENT ON COLUMN business.contracts.created_by    IS '创建人，关联meta.employees.id';
COMMENT ON COLUMN business.contracts.confirmed_by  IS '确认人，关联meta.employees.id';

-- ------------------------------------------------------------
-- 3. events 表加 contract_code 字段
-- 事件挂到合同下面
-- ------------------------------------------------------------
ALTER TABLE business.events
ADD COLUMN contract_code VARCHAR(50);

COMMENT ON COLUMN business.events.contract_code IS '所属合同编号，关联business.contracts.contract_code。询价阶段为空，成交后回填';

COMMIT;

-- ------------------------------------------------------------
-- 同步宪法表
-- ------------------------------------------------------------
CALL meta.sync_constitution();

-- ------------------------------------------------------------
-- 验证
-- ------------------------------------------------------------
SELECT table_schema, table_name
FROM information_schema.tables
WHERE table_schema IN ('meta', 'business')
  AND table_type = 'BASE TABLE'
ORDER BY table_schema, table_name;
