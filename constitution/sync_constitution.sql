-- ============================================================
-- AIERP 宪法表自动同步存储过程
-- 创建：meta.sync_constitution()
-- 用法：CALL meta.sync_constitution();
--       然后（为 field_cn 填中文名）: python scripts/sync_meta_cn.py
-- 作用：先清空并重建 meta_fields（业务表字段），再同步 meta_tables；
--       新增 meta_tables 行时写入 include_in_domain_model（business=true，meta=false），已有行不覆盖该开关；
--       字段注释来自 pg_description（COMMENT ON COLUMN）
--       field_cn 由库内留空，需单独运行 sync_meta_cn.py 批量调 LLM 生成
--
-- 【重要】meta_fields 第一步须删除「除 meta_tables / meta_fields 两行自身以外的全部字段行」。
--       若用「仅 table_name IN (当前库仍存在的表)」会删不掉已 DROP 表的孤儿行，旧数据会一直残留。
--
-- ask_if_missing 推断规则：
--   同时满足：is_nullable=NO、column_default IS NULL、字段名不在 v_system_fields
--   → true；否则 false。
--
-- 【维护说明】若需登记 business.procedures 并写 procedure_logs，请执行
-- constitution/register_sync_constitution_procedure.sql（会覆盖本过程为带日志版）。
-- ============================================================

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
    v_cn_generated       INT := 0;  -- 存储过程内不调用 LLM，恒为 0；实际条数见 sync_meta_cn.py 输出
    v_field_cn_pending   INT := 0;
    v_exists             BOOLEAN;

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

    -- --------------------------------------------------------
    -- 第一步：清空 meta_fields（除宪法表 meta_tables、meta_fields 自身字段行外全部删除）
    --       这样已 DROP 的表在 meta_fields 中的孤儿行也会一并清掉，再按 information_schema 重建
    -- --------------------------------------------------------
    DELETE FROM meta.meta_fields
    WHERE table_name NOT IN ('meta_tables', 'meta_fields');

    -- --------------------------------------------------------
    -- 第二步：从 information_schema + pg_description 重新 INSERT 所有字段
    -- field_cn 留空；description = 列注释；is_required / ask_if_missing 见下
    -- --------------------------------------------------------
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

    -- --------------------------------------------------------
    -- 第三步：field_cn 中文名由 LLM 生成（本存储过程不调用 LLM）
    -- 请在库外执行: python scripts/sync_meta_cn.py
    -- --------------------------------------------------------

    -- --------------------------------------------------------
    -- 第四步：同步 meta.meta_tables
    --       先删除「库中已不存在」的表目录行（孤儿），再按原逻辑 INSERT/UPDATE
    -- --------------------------------------------------------
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
            INSERT INTO meta.meta_tables (
                table_name, table_cn, description, keywords, confirm_before_write,
                include_in_domain_model
            )
            VALUES (
                v_table.table_name,
                v_table.table_name,
                COALESCE(v_table.table_comment, ''),
                '',
                true,
                (v_table.table_schema = 'business')
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

    -- --------------------------------------------------------
    -- 第五步：摘要（field_cn 由脚本生成，此处统计待生成条数）
    -- --------------------------------------------------------
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
    RAISE NOTICE '提示：ask_if_missing 仅在本过程插入时推断；请复核 status 等边界字段。';

END;
$$;

-- CALL meta.sync_constitution();
