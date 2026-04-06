-- ============================================================
-- 将「认证表迁移脚本」登记到 business.procedures（PROC-002）
-- 说明：DDL/种子数据本体在 create_auth_tables.sql，需 psql -f 执行；
--       此处提供可 CALL 的提示过程，便于与 PROC-001 一样被 AI/应用发现。
-- ============================================================

CREATE OR REPLACE PROCEDURE meta.auth_schema_migration_guide()
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE NOTICE '认证迁移：请用 psql 执行仓库文件 constitution/create_auth_tables.sql';
    RAISE NOTICE '（创建 user_accounts / user_role_grants，插入设计部测试账号与 super_admin 授权）';
END;
$$;

COMMENT ON PROCEDURE meta.auth_schema_migration_guide() IS
'不执行 DDL；仅提示运维在 psql 中运行 create_auth_tables.sql。对应 procedures.procedure_code = PROC-002。';

INSERT INTO business.procedures (
    procedure_code,
    event_type,
    operation_type,
    intent_desc,
    intent_keywords,
    sql_template,
    parameters,
    execution_notes,
    risk_level,
    is_active
)
VALUES (
    'PROC-002',
    NULL,
    'insert',
    '登录与权限基座迁移：创建 business.user_accounts（员工一对一登录、密码仅存哈希）、business.user_role_grants（账号角色如 super_admin）；并插入设计部测试员工（DES-001/DES-002）及 nicjiang、cursor 测试账号与超级管理员授权。默认测试密码见脚本注释，上线前必须修改。',
    '["登录","user_accounts","user_role_grants","权限","bcrypt","设计部","super_admin"]'::jsonb,
    'CALL meta.auth_schema_migration_guide();',
    '[]'::jsonb,
    '高风险：含建表与写库。实际结构变更与种子数据请执行 constitution/create_auth_tables.sql：psql -U nicjiang -d aierp -v ON_ERROR_STOP=1 -f constitution/create_auth_tables.sql。CALL 本登记过程仅输出 NOTICE，不会重复建表。',
    'high',
    true
)
ON CONFLICT (procedure_code) DO UPDATE SET
    intent_desc      = EXCLUDED.intent_desc,
    intent_keywords  = EXCLUDED.intent_keywords,
    sql_template     = EXCLUDED.sql_template,
    parameters       = EXCLUDED.parameters,
    execution_notes  = EXCLUDED.execution_notes,
    risk_level       = EXCLUDED.risk_level,
    is_active        = EXCLUDED.is_active,
    updated_at       = now();
