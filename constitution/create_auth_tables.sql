-- ============================================================
-- 登录与权限（测试数据）
-- 表：business.user_accounts        登录账号（与员工一对一）
-- 表：business.user_role_grants     账号角色授权（可扩展为细粒度 permission）
-- 默认测试密码（两账号相同）：test123456  （上线前务必修改）
--
-- 登记：business.procedures 中 PROC-002（见 register_auth_migration_procedure.sql）
-- ============================================================

BEGIN;

-- -----------------------------------------------------------------
-- 1) 登录账号表：凭证与 business.employees 人事档案分离，便于审计与扩展 SSO
-- -----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS business.user_accounts (
    id              integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    employee_id     integer NOT NULL UNIQUE
        REFERENCES business.employees (id) ON DELETE CASCADE,
    login_name      character varying(64) NOT NULL UNIQUE,
    password_hash   text,
    is_active       boolean NOT NULL DEFAULT true,
    created_at      timestamp with time zone NOT NULL DEFAULT now(),
    updated_at      timestamp with time zone NOT NULL DEFAULT now(),
    last_login_at   timestamp with time zone,
    notes           text
);

COMMENT ON TABLE business.user_accounts IS
'登录账号表：与 business.employees 一对一关联。密码仅存单向哈希（bcrypt/Argon2），禁止存明文；权限粒度见 business.user_role_grants。';

COMMENT ON COLUMN business.user_accounts.employee_id IS '关联员工主键；一名员工最多一个登录账号。';
COMMENT ON COLUMN business.user_accounts.login_name IS '登录名（建议小写英文），在系统内唯一。';
COMMENT ON COLUMN business.user_accounts.password_hash IS '密码哈希字符串（推荐 bcrypt 或 Argon2）；由应用层生成与校验。';
COMMENT ON COLUMN business.user_accounts.is_active IS 'false 时拒绝登录，用于离职/冻结，不等同于员工 status。';
COMMENT ON COLUMN business.user_accounts.last_login_at IS '最近一次登录成功时间，由应用层更新。';

CREATE INDEX IF NOT EXISTS idx_user_accounts_active ON business.user_accounts (is_active) WHERE is_active = true;

-- -----------------------------------------------------------------
-- 2) 账号角色授权表：先以角色代码描述权限集，后续可拆 role / permission 细表
-- -----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS business.user_role_grants (
    id               integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_account_id  integer NOT NULL
        REFERENCES business.user_accounts (id) ON DELETE CASCADE,
    role_code        character varying(32) NOT NULL,
    granted_at       timestamp with time zone NOT NULL DEFAULT now(),
    granted_by       integer REFERENCES business.employees (id) ON DELETE SET NULL,
    notes            text,
    CONSTRAINT uq_user_role_grants_account_role UNIQUE (user_account_id, role_code)
);

COMMENT ON TABLE business.user_role_grants IS
'账号角色授权：描述「某登录账号拥有哪些角色」。角色与具体 API/菜单权限的映射在应用层或后续 permission 表扩展。';

COMMENT ON COLUMN business.user_role_grants.role_code IS
'角色代码，如 super_admin（超级管理员）、admin、sales、viewer；应用层统一解析。';

COMMENT ON COLUMN business.user_role_grants.granted_by IS '授权人（员工 id），自助注册场景可为空。';

CREATE INDEX IF NOT EXISTS idx_user_role_grants_role ON business.user_role_grants (role_code);

-- -----------------------------------------------------------------
-- 3) 测试员工：设计部，Super Admin（档案侧标签）
-- -----------------------------------------------------------------
INSERT INTO business.employees (emp_code, name, department, role_tags, status)
SELECT 'DES-001', 'Nic Jiang', '设计部', 'SuperAdmin', 'Active'
WHERE NOT EXISTS (SELECT 1 FROM business.employees WHERE emp_code = 'DES-001');

INSERT INTO business.employees (emp_code, name, department, role_tags, status)
SELECT 'DES-002', 'Cursor Agent', '设计部', 'SuperAdmin', 'Active'
WHERE NOT EXISTS (SELECT 1 FROM business.employees WHERE emp_code = 'DES-002');

-- -----------------------------------------------------------------
-- 4) 测试登录账号（密码哈希对应明文：test123456）
-- -----------------------------------------------------------------
INSERT INTO business.user_accounts (employee_id, login_name, password_hash, is_active, notes)
SELECT e.id,
       'nicjiang',
       '$2b$12$Edqg39X/WnGO8YUUOUuXAutTH2XH0qkR5MCR4U6uWsyt2l.kyky96',
       true,
       '测试账号：默认密码 test123456；上线前请修改密码并轮换哈希。'
FROM business.employees e
WHERE e.emp_code = 'DES-001'
  AND NOT EXISTS (SELECT 1 FROM business.user_accounts ua WHERE ua.login_name = 'nicjiang');

INSERT INTO business.user_accounts (employee_id, login_name, password_hash, is_active, notes)
SELECT e.id,
       'cursor',
       '$2b$12$Edqg39X/WnGO8YUUOUuXAutTH2XH0qkR5MCR4U6uWsyt2l.kyky96',
       true,
       '测试账号（协作机器人侧）：默认密码 test123456；上线前请修改。'
FROM business.employees e
WHERE e.emp_code = 'DES-002'
  AND NOT EXISTS (SELECT 1 FROM business.user_accounts ua WHERE ua.login_name = 'cursor');

-- -----------------------------------------------------------------
-- 5) 超级管理员角色（两账号各一条）
-- -----------------------------------------------------------------
INSERT INTO business.user_role_grants (user_account_id, role_code, notes)
SELECT ua.id,
       'super_admin',
       '超级管理员：全量业务与元数据、账号与权限配置（测试环境）。'
FROM business.user_accounts ua
WHERE ua.login_name = 'nicjiang'
  AND NOT EXISTS (
      SELECT 1 FROM business.user_role_grants g
      WHERE g.user_account_id = ua.id AND g.role_code = 'super_admin'
  );

INSERT INTO business.user_role_grants (user_account_id, role_code, notes)
SELECT ua.id,
       'super_admin',
       '超级管理员：全量业务与元数据、账号与权限配置（测试环境）。'
FROM business.user_accounts ua
WHERE ua.login_name = 'cursor'
  AND NOT EXISTS (
      SELECT 1 FROM business.user_role_grants g
      WHERE g.user_account_id = ua.id AND g.role_code = 'super_admin'
  );

COMMIT;
