-- ============================================================
-- 将 meta.sync_constitution() 登记到 business.procedures，
-- 并在每次执行后写入 business.procedure_logs（成功/失败均记录）。
-- 执行：psql -f constitution/register_sync_constitution_procedure.sql
-- ============================================================

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
    'PROC-001',
    NULL,
    'update',
    '同步「宪法」元数据：先 DELETE 再 INSERT 重建 meta_fields（业务表字段），自 information_schema/pg_description 写入；field_cn 留空；再同步 meta_tables。field_cn 中文需另执行 python scripts/sync_meta_cn.py。成功/失败写入 business.procedure_logs。',
    '["宪法同步","meta_tables","meta_fields","元数据","表结构","字段同步","sync_meta_cn"]'::jsonb,
    'CALL meta.sync_constitution();',
    '[]'::jsonb,
    '无参数。执行顺序：1) CALL meta.sync_constitution(); 2) python scripts/sync_meta_cn.py（为 field_cn 填中文）。作用域：meta、business 下 BASE TABLE，排除宪法表自身行。',
    'medium',
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

CREATE OR REPLACE PROCEDURE meta.sync_constitution()
LANGUAGE plpgsql
AS $$
DECLARE
    v_table              RECORD;
    v_field              RECORD;
    v_tables_insert      INT := 0;
    v_tables_update      INT := 0;
    v_tables_deleted     INT := 0;
    v_fields_insert      INT := 0;
    v_cn_generated       INT := 0;
    v_field_cn_pending   INT := 0;
    v_exists             BOOLEAN;
    v_procedure_id       INTEGER;
    v_rows_total         INTEGER;

    v_system_fields TEXT[] := ARRAY[
        'id',
        'created_at',
        'updated_at',
        'contract_no',
        'amount',
        'total_amount',
        'updated_by',
        'supplied_at',
        'supplied_by'
    ];
BEGIN
    SELECT id INTO v_procedure_id
    FROM business.procedures
    WHERE procedure_code = 'PROC-001'
      AND is_active = true
    LIMIT 1;

    DELETE FROM meta.meta_fields
    WHERE table_name NOT IN ('meta_tables', 'meta_fields');

    FOR v_field IN
        SELECT
            c.table_name,
            c.column_name,
            c.data_type,
            c.is_nullable,
            c.column_default,
            pgd.description AS col_comment
        FROM information_schema.columns c
        JOIN pg_class pc ON pc.relname = c.table_name
        JOIN pg_namespace pn ON pn.oid = pc.relnamespace
            AND pn.nspname = c.table_schema
        LEFT JOIN pg_catalog.pg_description pgd
            ON pgd.objoid = pc.oid
            AND pgd.objsubid = c.ordinal_position
        WHERE c.table_schema IN ('meta', 'business')
          AND c.table_name NOT IN ('meta_tables', 'meta_fields')
        ORDER BY c.table_name, c.ordinal_position
    LOOP
        INSERT INTO meta.meta_fields
            (table_name, field_name, field_cn, data_type,
             is_required, default_value, description, ask_if_missing)
        VALUES (
            v_field.table_name,
            v_field.column_name,
            '',
            v_field.data_type,
            CASE WHEN v_field.is_nullable = 'NO'
                      AND v_field.column_default IS NULL
                 THEN true ELSE false END,
            v_field.column_default,
            COALESCE(v_field.col_comment, ''),
            CASE WHEN v_field.is_nullable = 'NO'
                      AND v_field.column_default IS NULL
                      AND NOT (v_field.column_name = ANY(v_system_fields))
                 THEN true ELSE false END
        );
        v_fields_insert := v_fields_insert + 1;
    END LOOP;

    DELETE FROM meta.meta_tables
    WHERE table_name NOT IN (
        SELECT t.table_name
        FROM information_schema.tables t
        WHERE t.table_schema IN ('meta', 'business')
          AND t.table_type = 'BASE TABLE'
    )
      AND table_name NOT IN ('meta_tables', 'meta_fields');
    GET DIAGNOSTICS v_tables_deleted = ROW_COUNT;

    FOR v_table IN
        SELECT
            t.table_name,
            t.table_schema,
            obj_description(pc.oid, 'pg_class') AS table_comment
        FROM information_schema.tables t
        JOIN pg_class pc ON pc.relname = t.table_name
        JOIN pg_namespace pn ON pn.oid = pc.relnamespace
            AND pn.nspname = t.table_schema
        WHERE t.table_schema IN ('meta', 'business')
          AND t.table_type = 'BASE TABLE'
          AND t.table_name NOT IN ('meta_tables', 'meta_fields')
        ORDER BY t.table_schema, t.table_name
    LOOP
        SELECT EXISTS (
            SELECT 1 FROM meta.meta_tables
            WHERE table_name = v_table.table_name
        ) INTO v_exists;

        IF NOT v_exists THEN
            INSERT INTO meta.meta_tables (table_name, table_cn, description, keywords, confirm_before_write)
            VALUES (
                v_table.table_name,
                v_table.table_name,
                COALESCE(v_table.table_comment, ''),
                '',
                true
            );
            v_tables_insert := v_tables_insert + 1;
            RAISE NOTICE '新增表：%', v_table.table_name;

        ELSE
            UPDATE meta.meta_tables
            SET description = COALESCE(NULLIF(description, ''), v_table.table_comment)
            WHERE table_name = v_table.table_name
              AND v_table.table_comment IS NOT NULL
              AND (description IS NULL OR description = '');

            IF FOUND THEN
                v_tables_update := v_tables_update + 1;
            END IF;
        END IF;
    END LOOP;

    SELECT COUNT(*)::INT INTO v_field_cn_pending
    FROM meta.meta_fields
    WHERE COALESCE(TRIM(field_cn), '') = '';

    RAISE NOTICE '==============================';
    RAISE NOTICE '宪法表同步完成';
    RAISE NOTICE '  meta_tables：删除孤儿 % 条，新增 % 条，更新 % 条', v_tables_deleted, v_tables_insert, v_tables_update;
    RAISE NOTICE '  meta_fields ：重建插入 % 条', v_fields_insert;
    RAISE NOTICE '  meta_fields field_cn：自动生成 % 条', v_cn_generated;
    RAISE NOTICE '  meta_fields field_cn 待生成（空字符串）: % 条 — 请执行: python scripts/sync_meta_cn.py', v_field_cn_pending;
    RAISE NOTICE '==============================';

    v_rows_total := v_tables_deleted + v_tables_insert + v_tables_update + v_fields_insert;

    IF v_procedure_id IS NOT NULL THEN
        INSERT INTO business.procedure_logs (
            procedure_id,
            procedure_code,
            parameters_used,
            sql_executed,
            executed_by,
            result_summary,
            rows_affected,
            success
        ) VALUES (
            v_procedure_id,
            'PROC-001',
            '{}'::jsonb,
            'CALL meta.sync_constitution();',
            'ai_auto',
            format(
                'meta_tables：删孤儿%s条新增%s条更新%s条；meta_fields重建%s条；field_cn待生成%s条（请运行 sync_meta_cn.py）',
                v_tables_deleted, v_tables_insert, v_tables_update, v_fields_insert, v_field_cn_pending
            ),
            v_rows_total,
            true
        );

        UPDATE business.procedures
        SET usage_count = COALESCE(usage_count, 0) + 1,
            last_used_at = now(),
            updated_at = now()
        WHERE id = v_procedure_id;
    END IF;

EXCEPTION WHEN OTHERS THEN
    IF v_procedure_id IS NOT NULL THEN
        INSERT INTO business.procedure_logs (
            procedure_id,
            procedure_code,
            parameters_used,
            sql_executed,
            executed_by,
            result_summary,
            rows_affected,
            success,
            error_message
        ) VALUES (
            v_procedure_id,
            'PROC-001',
            '{}'::jsonb,
            'CALL meta.sync_constitution();',
            'ai_auto',
            NULL,
            NULL,
            false,
            SQLERRM
        );
    END IF;
    RAISE;
END;
$$;
