-- V16c：submit 工具说明补充（simple_entity 扁平 data / 可选 table_name）
-- 执行：psql -d aierp -f constitution/v16c_meta_tools_submit_hint.sql

UPDATE meta.meta_tools
SET description = (
    '提交写操作预览（新建/更新/删除/合同等），不直接写库；确认后由引擎按 workflow_steps 执行。'
    ' data 为对象：可与 write_entity/update_entity 类似使用 table_name + fields + record_id；'
    ' 在 create_simple_entity / update_simple_entity / delete_simple_entity 下也允许扁平字段'
    '（字段名直接放在 data 顶层），若 frame 已绑定 target_table 可不传 table_name；'
    ' 亦可显式传 table_name 或 table 指定目标表。'
    ' 合同类意图请按 contract/items 等 workflow 约定传参。'
)
WHERE tool_name = 'submit';
