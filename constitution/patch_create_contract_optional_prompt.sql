-- 为 create_contract 追加 prompt_rules：可选主表字段不追问（与 add_workflow_context_schema 同步）
-- 执行：psql -d aierp -f constitution/patch_create_contract_optional_prompt.sql
BEGIN;

UPDATE business.workflows
SET context_schema = jsonb_set(
  context_schema,
  '{prompt_rules}',
  coalesce(context_schema->'prompt_rules', '[]'::jsonb)
    || jsonb_build_array(
      '付款方式、交货日期、签约日期为可选字段，用户未提供时不追问，直接调用 submit 提交。'
    )
)
WHERE intent = 'create_contract'
  AND NOT EXISTS (
    SELECT 1
    FROM jsonb_array_elements_text(
      coalesce(context_schema->'prompt_rules', '[]'::jsonb)
    ) AS r(elem)
    WHERE elem LIKE '%付款方式、交货日期、签约日期为可选字段%'
  );

COMMIT;
