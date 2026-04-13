-- 将旧版 v17 建在 meta 下的权限表迁入 business（幂等）
-- 新版 v17 已直接 CREATE 于 business 时：以下 ALTER 不执行；提示词替换仍可能执行。

BEGIN;

DO $body$
BEGIN
  IF to_regclass('meta.approval_rules') IS NOT NULL
     AND to_regclass('business.approval_rules') IS NULL THEN
    ALTER TABLE meta.approval_rules SET SCHEMA business;
  END IF;
END
$body$;

DO $body$
BEGIN
  IF to_regclass('meta.role_permissions') IS NOT NULL
     AND to_regclass('business.role_permissions') IS NULL THEN
    ALTER TABLE meta.role_permissions SET SCHEMA business;
  END IF;
END
$body$;

UPDATE meta.prompts
SET content = replace(replace(content, 'meta.role_permissions', 'business.role_permissions'), 'meta.approval_rules', 'business.approval_rules'),
    version = version + 1,
    updated_at = NOW(),
    updated_by = 'system'
WHERE content LIKE '%meta.role_permissions%' OR content LIKE '%meta.approval_rules%';

COMMIT;

CALL meta.sync_constitution();
