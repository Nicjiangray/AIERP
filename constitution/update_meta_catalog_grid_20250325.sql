-- ============================================================
-- 2025-03-25：设计记忆入库 + 更新 meta.meta_tables / meta.meta_fields
-- 对应工作：通用表网格（模糊筛选、服务端排序分页、Excel 导出）、
--           openpyxl 惰性导入、元数据与前端 TableView 联动说明补全。
-- 执行：psql -U nicjiang -d aierp -f constitution/update_meta_catalog_grid_20250325.sql
-- 说明：design_memories.embedding 留空时可稍后运行 scripts/upsert_design_memory.py 补向量。
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- 1) 设计过程记忆（无向量亦可插入，便于先落库）
-- ------------------------------------------------------------
INSERT INTO business.design_memories (title, content, change_layers, source_ref, tags, embedding)
SELECT
    'AI·ERP 通用数据表：筛选、排序、分页、导出与元数据（2025-03-25）',
    E'【工作要点回顾】\n'
    '1) 后端 FastAPI：通用列表 GET /table/{schema}/{table} 支持 filters（JSON，多列 AND 模糊 ILIKE）、q（可筛选列 OR 模糊）、sort_by/sort_order、limit/offset；SQL 顺序为 WHERE → ORDER BY（全量匹配集排序）→ LIMIT/OFFSET；返回 total 供分页。\n'
    '2) 导出 GET /export/xlsx：与列表相同筛选与排序；简单 xlsx（Excel 2007+）；openpyxl 仅在导出路由内导入，避免缺依赖导致整站 API 无法启动。\n'
    '3) 前端 TableView：列头「模糊」输入 + 顶部任意列 q；remote 表格，服务端排序与分页（默认每页 500，可选 50/100/200/500）；导出携带当前筛选与排序。\n'
    '4) 元数据：表头中文来自 meta.meta_fields.menu_label_cn；列顺序来自 meta_fields.id 与本地列顺序存储；nav_section/nav_order 驱动左侧数据导航。\n'
    '5) 安全：password_hash、embedding、intent_embedding、vector 列不参与筛选或特殊处理（导出对向量占位）。\n',
    ARRAY['frontend', 'backend', 'database']::text[],
    'constitution/update_meta_catalog_grid_20250325.sql; main.py; frontend/src/views/TableView.vue; frontend/src/stores/app.js; requirements.txt',
    ARRAY['grid', 'export', 'ILIKE', 'pagination', 'meta']::text[],
    NULL
WHERE NOT EXISTS (
    SELECT 1 FROM business.design_memories d
    WHERE d.title = 'AI·ERP 通用数据表：筛选、排序、分页、导出与元数据（2025-03-25）'
);

-- ------------------------------------------------------------
-- 2) meta.meta_tables：两张「宪法表」自身的说明与关键词
-- ------------------------------------------------------------
UPDATE meta.meta_tables
SET
    table_cn    = '元数据·表目录',
    description = '登记 meta / business 下各基表的逻辑中文名、说明、关键词与导航配置（nav_section / nav_order / nav_parent_table）。'
        ' 左侧数据导航与通用表网格依赖本表；sync_constitution 不从 information_schema 回写本表与 meta_fields 两行自身，需手工或本类脚本维护。'
        ' 与 TableView 联动：nav 决定分组与顺序。',
    keywords    = '元数据,表目录,导航,宪法,sync_constitution,TableView,nav_section'
WHERE table_name = 'meta_tables';

UPDATE meta.meta_tables
SET
    table_cn    = '元数据·字段目录',
    description = '每表每字段的中文名、菜单列标题（menu_label_cn/en）、类型与说明。'
        ' 前端表头、表单标签、导出表头与列顺序均依赖本表；通用查询按列名传 filters，与 field_name 一致。'
        ' sync_constitution 不自动同步本表自身行。',
    keywords    = '元数据,字段,menu_label_cn,表头,导出,筛选,排序'
WHERE table_name = 'meta_fields';

-- ------------------------------------------------------------
-- 3) 补全 meta.meta_fields 中缺失的列定义（相对 information_schema）
-- ------------------------------------------------------------
INSERT INTO meta.meta_fields (
    table_name, field_name, field_cn, data_type, is_required, default_value,
    allowed_values, description, ask_if_missing, menu_label_cn, menu_label_en
)
SELECT v.*
FROM (VALUES
    ('meta_tables', 'nav_section', '导航大区', 'character varying', false, NULL::text, NULL::text,
        'design=系统设计；prep=系统准备；run=系统运行。与左侧三大块一致。',
        false, '导航大区', 'Nav Section'),
    ('meta_tables', 'nav_order', '导航顺序', 'integer', true, '1000', NULL,
        '同一 nav_section 内排序，数值越小越靠前。',
        false, '导航顺序', 'Nav Order'),
    ('meta_tables', 'nav_parent_table', '父表', 'character varying', false, NULL, NULL,
        '可选：子表指向父表 table_name，用于层级导航。',
        false, '父表', 'Parent Table'),
    ('meta_tables', 'created_at', '创建时间', 'timestamp with time zone', false, NULL, NULL,
        '行创建时间。',
        false, '创建时间', 'Created At'),
    ('meta_fields', 'menu_label_cn', '菜单中文名', 'character varying', false, NULL, NULL,
        '表头/菜单展示用中文，优先于 field_cn；TableView 与导出 xlsx 表头使用。',
        false, '菜单中文名', 'Menu Label CN'),
    ('meta_fields', 'menu_label_en', '菜单英文名', 'character varying', false, NULL, NULL,
        '英文菜单或双语场景使用。',
        false, '菜单英文名', 'Menu Label EN'),
    ('meta_fields', 'created_at', '创建时间', 'timestamp with time zone', false, NULL, NULL,
        '字段元数据行创建时间。',
        false, '创建时间', 'Created At')
) AS v(
    table_name, field_name, field_cn, data_type, is_required, default_value,
    allowed_values, description, ask_if_missing, menu_label_cn, menu_label_en
)
WHERE NOT EXISTS (
    SELECT 1 FROM meta.meta_fields mf
    WHERE mf.table_name = v.table_name AND mf.field_name = v.field_name
);

-- ------------------------------------------------------------
-- 4) 刷新已有字段行的说明与菜单标签（meta_tables 表）
-- ------------------------------------------------------------
UPDATE meta.meta_fields SET
    menu_label_cn = '主键',
    menu_label_en = 'ID',
    description   = '表目录行主键。'
WHERE table_name = 'meta_tables' AND field_name = 'id';

UPDATE meta.meta_fields SET
    menu_label_cn = '表名',
    menu_label_en = 'Table Name',
    description   = '物理表名，与库中 meta/business 下基表一致；API 路径 /table/{schema}/{table_name}。'
WHERE table_name = 'meta_tables' AND field_name = 'table_name';

UPDATE meta.meta_fields SET
    menu_label_cn = '表中文名',
    menu_label_en = 'Table Title CN',
    description   = '业务可读中文名；导航与面包屑展示。'
WHERE table_name = 'meta_tables' AND field_name = 'table_cn';

UPDATE meta.meta_fields SET
    menu_label_cn = '表说明',
    menu_label_en = 'Description',
    description   = '表级说明，供人与 AI 理解用途。'
WHERE table_name = 'meta_tables' AND field_name = 'description';

UPDATE meta.meta_fields SET
    menu_label_cn = '关键词',
    menu_label_en = 'Keywords',
    description   = '检索用关键词（文本）。'
WHERE table_name = 'meta_tables' AND field_name = 'keywords';

UPDATE meta.meta_fields SET
    menu_label_cn = '写入前确认',
    menu_label_en = 'Confirm Before Write',
    description   = '若为 true，高危写入前需额外确认（产品策略）。'
WHERE table_name = 'meta_tables' AND field_name = 'confirm_before_write';

UPDATE meta.meta_fields SET
    menu_label_cn = '前置条件',
    menu_label_en = 'Preconditions',
    description   = '写入或同步前置条件说明（JSON 或文本，视实现而定）。'
WHERE table_name = 'meta_tables' AND field_name = 'preconditions';

-- ------------------------------------------------------------
-- 5) 刷新 meta_fields 表（字段目录自身）
-- ------------------------------------------------------------
UPDATE meta.meta_fields SET
    menu_label_cn = '主键',
    menu_label_en = 'ID',
    description   = '字段目录行主键。'
WHERE table_name = 'meta_fields' AND field_name = 'id';

UPDATE meta.meta_fields SET
    menu_label_cn = '归属表名',
    menu_label_en = 'Table Name',
    description   = '该字段所属基表的 table_name，与 meta_tables.table_name 对应。'
WHERE table_name = 'meta_fields' AND field_name = 'table_name';

UPDATE meta.meta_fields SET
    menu_label_cn = '字段名',
    menu_label_en = 'Field Name',
    description   = '列物理名；与 API filters JSON 的键一致。'
WHERE table_name = 'meta_fields' AND field_name = 'field_name';

UPDATE meta.meta_fields SET
    menu_label_cn = '字段中文名',
    menu_label_en = 'Field CN',
    description   = '字段级中文名（与菜单列标题 menu_label_cn 可分工）。'
WHERE table_name = 'meta_fields' AND field_name = 'field_cn';

UPDATE meta.meta_fields SET
    menu_label_cn = '数据类型',
    menu_label_en = 'Data Type',
    description   = '逻辑类型说明（来自同步或手工）。'
WHERE table_name = 'meta_fields' AND field_name = 'data_type';

UPDATE meta.meta_fields SET
    menu_label_cn = '必填',
    menu_label_en = 'Required',
    description   = '是否必填（设计期约束说明）。'
WHERE table_name = 'meta_fields' AND field_name = 'is_required';

UPDATE meta.meta_fields SET
    menu_label_cn = '默认值',
    menu_label_en = 'Default',
    description   = '默认值说明。'
WHERE table_name = 'meta_fields' AND field_name = 'default_value';

UPDATE meta.meta_fields SET
    menu_label_cn = '允许值',
    menu_label_en = 'Allowed Values',
    description   = '枚举或取值范围说明。'
WHERE table_name = 'meta_fields' AND field_name = 'allowed_values';

UPDATE meta.meta_fields SET
    menu_label_cn = '字段说明',
    menu_label_en = 'Description',
    description   = '列级说明，供人与 AI 理解。'
WHERE table_name = 'meta_fields' AND field_name = 'description';

UPDATE meta.meta_fields SET
    menu_label_cn = '缺失时询问',
    menu_label_en = 'Ask If Missing',
    description   = '采集或对话补全策略标记。'
WHERE table_name = 'meta_fields' AND field_name = 'ask_if_missing';

COMMIT;
