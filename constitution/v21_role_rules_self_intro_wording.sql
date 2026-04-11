-- role_rules：自我介绍段落措辞「尽管试试」→「尽管告诉我」（v20 已执行时修补）
-- 执行：psql -d aierp -f constitution/v21_role_rules_self_intro_wording.sql

UPDATE meta.prompts
SET
    content = replace(
        COALESCE(content, ''),
        '总之，尽管试试，也许会让您大吃一惊。',
        '总之，尽管告诉我，也许会让您大吃一惊。'
    ),
    version = version + 1,
    updated_at = NOW(),
    updated_by = 'system'
WHERE prompt_code = 'role_rules'
  AND is_active = true
  AND position('总之，尽管试试，也许会让您大吃一惊。' IN COALESCE(content, '')) > 0;
