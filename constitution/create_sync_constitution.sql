-- ============================================================
-- AIERP 宪法表自动同步存储过程
-- 创建：meta.sync_constitution()
-- 用法：CALL meta.sync_constitution();
-- 作用：自动读取数据库实际结构，同步到 meta_tables 和 meta_fields
--       新表 → 自动INSERT
--       已有表/字段有变化 → 自动UPDATE
--       每次运行输出同步摘要
-- ============================================================

CREATE OR REPLACE PROCEDURE meta.sync_constitution()
LANGUAGE plpgsql
AS $$
DECLARE
    v_table         RECORD;
    v_field         RECORD;
    v_tables_insert INT := 0;
    v_tables_update INT := 0;
    v_fields_insert INT := 0;
    v_fields_update INT := 0;
    v_exists        BOOLEAN;
BEGIN

    -- --------------------------------------------------------
    -- 第一步：同步 meta.meta_tables
    -- 来源：information_schema.tables + pg_description
    -- 范围：meta 和 business 两个 schema，排除宪法表本身
    -- --------------------------------------------------------
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
          -- 宪法表自己不需要登记自己（可根据需要去掉此排除）
          AND t.table_name NOT IN ('meta_tables', 'meta_fields')
        ORDER BY t.table_schema, t.table_name
    LOOP
        SELECT EXISTS (
            SELECT 1 FROM meta.meta_tables
            WHERE table_name = v_table.table_name
        ) INTO v_exists;

        IF NOT v_exists THEN
            -- 新表：INSERT，描述用数据库注释，没有注释就留空等人工补充
            INSERT INTO meta.meta_tables (table_name, table_cn, description, keywords, confirm_before_write)
            VALUES (
                v_table.table_name,
                v_table.table_name,                          -- 中文名先用英文名占位，人工再改
                COALESCE(v_table.table_comment, ''),
                '',
                true
            );
            v_tables_insert := v_tables_insert + 1;
            RAISE NOTICE '新增表：%', v_table.table_name;

        ELSE
            -- 已有：仅更新描述（如果数据库注释有内容且宪法表描述为空）
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
    -- 第二步：同步 meta.meta_fields
    -- 来源：information_schema.columns + pg_description
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
        SELECT EXISTS (
            SELECT 1 FROM meta.meta_fields
            WHERE table_name = v_field.table_name
              AND field_name = v_field.column_name
        ) INTO v_exists;

        IF NOT v_exists THEN
            -- 新字段：INSERT
            INSERT INTO meta.meta_fields
                (table_name, field_name, field_cn, data_type, is_required, default_value, description, ask_if_missing)
            VALUES (
                v_field.table_name,
                v_field.column_name,
                v_field.column_name,                         -- 中文名先用英文名占位
                v_field.data_type,
                CASE WHEN v_field.is_nullable = 'NO' AND v_field.column_default IS NULL
                     THEN true ELSE false END,               -- 非空且无默认值 = 必填
                v_field.column_default,
                COALESCE(v_field.col_comment, ''),
                false
            );
            v_fields_insert := v_fields_insert + 1;

        ELSE
            -- 已有：更新类型和注释（如果有变化）
            UPDATE meta.meta_fields
            SET
                data_type   = v_field.data_type,
                description = CASE
                    WHEN v_field.col_comment IS NOT NULL AND (description IS NULL OR description = '')
                    THEN v_field.col_comment
                    ELSE description
                END
            WHERE table_name = v_field.table_name
              AND field_name = v_field.column_name;

            IF FOUND THEN
                v_fields_update := v_fields_update + 1;
            END IF;
        END IF;
    END LOOP;

    -- --------------------------------------------------------
    -- 第三步：输出同步摘要
    -- --------------------------------------------------------
    RAISE NOTICE '==============================';
    RAISE NOTICE '宪法表同步完成';
    RAISE NOTICE '  meta_tables：新增 % 条，更新 % 条', v_tables_insert, v_tables_update;
    RAISE NOTICE '  meta_fields ：新增 % 条，更新 % 条', v_fields_insert, v_fields_update;
    RAISE NOTICE '==============================';

END;
$$;

-- 创建完成后立即执行一次
CALL meta.sync_constitution();
