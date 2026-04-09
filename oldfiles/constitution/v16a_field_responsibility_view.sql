-- 字段职责审核视图（V16a）
-- 执行：psql -d aierp -f constitution/v16a_field_responsibility_view.sql

CREATE OR REPLACE VIEW meta.v_field_responsibility AS
SELECT
    mf.table_name,
    mf.field_name,
    mf.field_cn,
    mf.data_type,
    mf.is_required,
    mf.ask_if_missing,
    CASE
        WHEN mf.field_name IN ('id','created_at','updated_at','deleted_at','created_by')
            THEN '系统字段'
        WHEN mf.field_name LIKE '%_id'
            THEN '关联ID（查询带入）'
        WHEN mf.field_name IN ('amount','total_amount','contract_no')
            THEN '系统计算/自动生成'
        WHEN mf.ask_if_missing = true
            THEN '✅ 用户提供'
        ELSE '可选/有默认值'
    END AS responsibility
FROM meta.meta_fields mf
ORDER BY mf.table_name, mf.id;

COMMENT ON VIEW meta.v_field_responsibility IS
  '字段职责一览：标注每个字段由谁提供（用户/系统/查询带入），供流程设计者审核 ask_if_missing 标记';
