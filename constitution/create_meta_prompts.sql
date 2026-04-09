-- meta.prompts：提示词管理表 — 统一管理所有注入 LLM 的提示词模块
-- 执行：psql -d aierp -f constitution/create_meta_prompts.sql

CREATE TABLE IF NOT EXISTS meta.prompts (
    id            SERIAL PRIMARY KEY,
    prompt_code   VARCHAR(50)  NOT NULL UNIQUE,
    category      VARCHAR(20)  NOT NULL,
    title         VARCHAR(200) NOT NULL,
    summary       VARCHAR(500),
    content       TEXT         NOT NULL,
    sort_order    INTEGER      NOT NULL DEFAULT 0,
    inject_mode   VARCHAR(20)  NOT NULL DEFAULT 'always',
    source        VARCHAR(20)  NOT NULL DEFAULT 'manual',
    llm_editable  BOOLEAN      NOT NULL DEFAULT FALSE,
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    version       INTEGER      NOT NULL DEFAULT 1,
    agent_role    VARCHAR(30)  NOT NULL DEFAULT 'assistant',
    parent_code   VARCHAR(50),
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_by    VARCHAR(50)  NOT NULL DEFAULT 'system'
);

COMMENT ON TABLE meta.prompts IS '提示词管理表 - 统一管理所有注入LLM的提示词模块';
COMMENT ON COLUMN meta.prompts.id IS '自增主键';
COMMENT ON COLUMN meta.prompts.prompt_code IS '提示词唯一编码，如 role_rules / schema_shadow / recipe_create_contract';
COMMENT ON COLUMN meta.prompts.category IS '分类：system=系统规则, schema=表结构, recipe=业务工艺, tool=工具定义, format=输出格式';
COMMENT ON COLUMN meta.prompts.title IS '提示词标题，用于人工管理和LLM目录展示';
COMMENT ON COLUMN meta.prompts.summary IS '简短描述，按需注入时拼成目录供LLM选择';
COMMENT ON COLUMN meta.prompts.content IS '提示词正文，完整的文本或JSON内容';
COMMENT ON COLUMN meta.prompts.sort_order IS '注入排序，数字越小越靠前，靠前的权重越高';
COMMENT ON COLUMN meta.prompts.inject_mode IS '注入方式：always=每次对话必注入, on_demand=按需加载';
COMMENT ON COLUMN meta.prompts.source IS '数据来源：manual=人工编写, db_sync=数据库同步, engine_sync=引擎同步, llm=LLM生成';
COMMENT ON COLUMN meta.prompts.llm_editable IS 'LLM是否可修改此提示词';
COMMENT ON COLUMN meta.prompts.is_active IS '是否启用，停用后不注入';
COMMENT ON COLUMN meta.prompts.version IS '版本号，每次修改自增，用于追溯';
COMMENT ON COLUMN meta.prompts.agent_role IS 'agent角色标识：assistant=主助手，memory_manager=记忆管理员，tool_expert=工具专家，auditor=审计员';
COMMENT ON COLUMN meta.prompts.parent_code IS '父级编码，用于目录分组，如所有recipe类指向同一个目录节点';
COMMENT ON COLUMN meta.prompts.created_at IS '创建时间';
COMMENT ON COLUMN meta.prompts.updated_at IS '最后更新时间';
COMMENT ON COLUMN meta.prompts.updated_by IS '最后修改者：admin/system/llm，用于区分修改来源';

CREATE INDEX IF NOT EXISTS idx_prompts_category ON meta.prompts (category);
CREATE INDEX IF NOT EXISTS idx_prompts_inject_mode ON meta.prompts (inject_mode, is_active);
CREATE INDEX IF NOT EXISTS idx_prompts_parent_code ON meta.prompts (parent_code);

COMMENT ON INDEX meta.idx_prompts_category IS '按分类快速查询';
COMMENT ON INDEX meta.idx_prompts_inject_mode IS '快速获取需要注入的提示词';
COMMENT ON INDEX meta.idx_prompts_parent_code IS '按父级分组查询';
