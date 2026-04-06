-- =============================================================================
-- LLM 助手数据库账号初始化（PostgreSQL）
-- 请以超级用户 nicjiang 执行，且本机已存在数据库 aierp。
--
-- 用法（psql）：
--   psql -U nicjiang -d postgres -f setup_llm_user.sql
-- 脚本内含 \connect，会在同一会话中切换到数据库 aierp。
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. 创建角色（在任意数据库中执行均可，此处默认已连 postgres）
-- -----------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'llm_assistant') THEN
    EXECUTE $ddl$CREATE ROLE llm_assistant WITH LOGIN PASSWORD 'llm_safe_2024'$ddl$;
  END IF;
END
$$;

ALTER ROLE llm_assistant WITH PASSWORD 'llm_safe_2024';

GRANT CONNECT ON DATABASE aierp TO llm_assistant;

-- -----------------------------------------------------------------------------
-- 2. 以下必须在数据库 aierp 内执行（表与 schema 属于该库）
-- -----------------------------------------------------------------------------
\connect aierp

-- 助手专属工作区：新建表、临时表、实验对象一律在此 schema，勿写入 business
CREATE SCHEMA IF NOT EXISTS ai_assistant;

GRANT USAGE, CREATE ON SCHEMA public TO llm_assistant;

-- 现有表：仅 SELECT / INSERT / UPDATE（不含 DELETE、TRUNCATE；无 DROP 表权限）
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO llm_assistant;

-- 自增/序列：INSERT 时常需要
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO llm_assistant;

-- 未来由 nicjiang 在 public 下新建的对象：自动授予助手相同 DML（仍无 DELETE）
ALTER DEFAULT PRIVILEGES FOR ROLE nicjiang IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE ON TABLES TO llm_assistant;

ALTER DEFAULT PRIVILEGES FOR ROLE nicjiang IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO llm_assistant;

-- ai_assistant：助手在此 schema 下 CREATE 的表归助手所有，可删改；与正式业务表隔离
GRANT USAGE, CREATE ON SCHEMA ai_assistant TO llm_assistant;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA ai_assistant TO llm_assistant;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA ai_assistant TO llm_assistant;
ALTER DEFAULT PRIVILEGES FOR ROLE nicjiang IN SCHEMA ai_assistant
  GRANT SELECT, INSERT, UPDATE ON TABLES TO llm_assistant;
ALTER DEFAULT PRIVILEGES FOR ROLE nicjiang IN SCHEMA ai_assistant
  GRANT USAGE, SELECT ON SEQUENCES TO llm_assistant;

-- business：可读写已有业务表，但不得在 business 下新建表（避免与 ERP 主数据混放）
GRANT USAGE ON SCHEMA business TO llm_assistant;
REVOKE CREATE ON SCHEMA business FROM llm_assistant;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA business TO llm_assistant;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA business TO llm_assistant;
ALTER DEFAULT PRIVILEGES FOR ROLE nicjiang IN SCHEMA business
  GRANT SELECT, INSERT, UPDATE ON TABLES TO llm_assistant;
ALTER DEFAULT PRIVILEGES FOR ROLE nicjiang IN SCHEMA business
  GRANT USAGE, SELECT ON SEQUENCES TO llm_assistant;

-- meta：仅存元数据目录 meta_tables / meta_fields；助手只读，不得在 meta 下建表
GRANT USAGE ON SCHEMA meta TO llm_assistant;
REVOKE CREATE ON SCHEMA meta FROM llm_assistant;
GRANT SELECT ON ALL TABLES IN SCHEMA meta TO llm_assistant;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA meta TO llm_assistant;
ALTER DEFAULT PRIVILEGES FOR ROLE nicjiang IN SCHEMA meta
  GRANT SELECT ON TABLES TO llm_assistant;
ALTER DEFAULT PRIVILEGES FOR ROLE nicjiang IN SCHEMA meta
  GRANT USAGE, SELECT ON SEQUENCES TO llm_assistant;

-- 若将来由 llm_assistant 自己建表，其它角色（如 nicjiang）如需继承策略可另加 FOR ROLE；
-- 注意：在 PostgreSQL 中，对象所有者对自己拥有的表始终具备完整权限（含 DELETE/DROP），
-- 无法仅通过 GRANT 剥夺；本脚本约束的是「非所有者」视角下的助手权限。
