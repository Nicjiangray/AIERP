-- 从 meta.meta_fields 填充 business.workflow_steps.fields
-- 依赖：meta.meta_fields 含 is_primary_key（sync_constitution 常规即有）
-- 执行：psql -d aierp -f constitution/sync_workflow_step_fields.sql
-- 调用：SELECT meta.sync_workflow_step_fields();        -- 仅填充 fields 为 [] 的行
--       SELECT meta.sync_workflow_step_fields(true);   -- 强制覆盖全部

CREATE OR REPLACE FUNCTION meta.sync_workflow_step_fields(p_force boolean DEFAULT false)
RETURNS void
LANGUAGE plpgsql
AS $fn$
DECLARE
  rec RECORD;
  fld RECORD;
  arr jsonb;
  elem jsonb;
  dt text;
  fn text;
  fnl text;
  inc boolean;
  av jsonb;
  keys jsonb;
BEGIN
  FOR rec IN
    SELECT id, workflow_intent, seq, action, table_name, fields
    FROM business.workflow_steps
    ORDER BY workflow_intent, seq
  LOOP
    IF rec.table_name IS NULL OR btrim(rec.table_name) = '' THEN
      CONTINUE;
    END IF;

    IF NOT p_force
       AND rec.fields IS NOT NULL
       AND rec.fields <> '[]'::jsonb THEN
      CONTINUE;
    END IF;

    arr := '[]'::jsonb;

    FOR fld IN
      SELECT
        mf.field_name,
        mf.field_cn,
        mf.data_type,
        COALESCE(mf.is_required, false) AS is_required,
        mf.allowed_values
      FROM meta.meta_fields mf
      WHERE mf.table_name = rec.table_name
        AND (
          rec.action IN ('lookup', 'query')
          OR (rec.action IN ('collect', 'write') AND mf.ask_if_missing = true)
        )
      ORDER BY mf.id
    LOOP
      -- 与 Python 侧一致：系统列排除；主键列名一般为 id（meta 无 is_primary_key 列时不再解析 PK）
      IF fld.field_name IN ('id', 'created_at', 'updated_at', 'deleted_at') THEN
        CONTINUE;
      END IF;

      fn := fld.field_name;
      fnl := lower(fn);

      IF rec.action IN ('lookup', 'query') THEN
        inc := (
          fnl LIKE '%_name'
          OR fnl LIKE '%_code'
          OR fnl LIKE '%_no'
          OR fnl LIKE '%_id'
          OR fnl IN (
            'name', 'code', 'email', 'phone', 'mobile',
            'company_name', 'short_name', 'product_name'
          )
        );
        IF NOT inc THEN
          CONTINUE;
        END IF;
      END IF;

      dt := lower(trim(split_part(COALESCE(fld.data_type::text, 'varchar'), '(', 1)));

      elem := jsonb_build_object(
        'field', fn,
        'field_cn', COALESCE(nullif(trim(fld.field_cn), ''), fn),
        'type', dt
      );

      IF fld.is_required THEN
        elem := elem || jsonb_build_object('required', true);
      END IF;

      IF fld.allowed_values IS NOT NULL THEN
        av := fld.allowed_values::jsonb;
        IF jsonb_typeof(av) = 'object' AND av <> '{}'::jsonb THEN
          SELECT COALESCE(jsonb_agg(to_jsonb(k) ORDER BY k), '[]'::jsonb)
          INTO keys
          FROM jsonb_object_keys(av) AS k;
          IF keys IS NOT NULL AND jsonb_array_length(keys) > 0 THEN
            elem := elem || jsonb_build_object('allowed_values', keys);
          END IF;
        ELSIF jsonb_typeof(av) = 'array' AND jsonb_array_length(av) > 0 THEN
          elem := elem || jsonb_build_object('allowed_values', av);
        END IF;
      END IF;

      arr := arr || jsonb_build_array(elem);
    END LOOP;

    UPDATE business.workflow_steps
    SET fields = arr,
        updated_at = now()
    WHERE id = rec.id;
  END LOOP;

  RETURN;
END;
$fn$;

COMMENT ON FUNCTION meta.sync_workflow_step_fields(boolean) IS
  '按 workflow_steps.action/table_name 从 meta.meta_fields 组装 fields JSON；p_force=true 时覆盖已有非空 fields。';
