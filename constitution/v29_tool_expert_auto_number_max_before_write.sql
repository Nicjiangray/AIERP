-- V29：tool_expert 自动生成编号前须用 query 做 MAX 查重，再单次 write（不循环重试）
-- 依赖：建议已执行 v28；未执行 v28 亦可单独执行本脚本
-- 执行：psql -U … -d aierp -f constitution/v29_tool_expert_auto_number_max_before_write.sql

UPDATE meta.prompts
SET
    content = content || E'\n\n' || $c$

## 自动生成编号（先查重再写入）

当需要为记录**自动生成业务编号**（工号、单号、流水号等）时：

1. **先用一次 `type: query`**，在**同一前缀/规则**下查询该编号列的已有最大值，例如工号前缀 `DES-`：
   `SELECT MAX(emp_code) FROM business.employees WHERE emp_code LIKE 'DES-%'`
   （按实际 **schema.表名**、**编号列名**、**前缀/匹配模式** 改写；必要时用 `SUBSTRING`/`SPLIT_PART` 只比较序号段。）
2. 根据 MAX 结果在**序号部分 +1** 生成新编号；若查询结果为空或无匹配行（表中**尚无该前缀**的记录），新序号从 **001**（或业务约定的起始位宽，如 `DES-001`）开始。
3. **随后直接输出一条 `type: write`**，写入算出的新编号。
4. **不要**依赖「写入失败再改编号重试」来碰运气，也**不要**为编号生成单独做多轮循环；一次 query 定号 + 一次 write 即可。

$c$,
    version = version + 1,
    updated_at = NOW(),
    updated_by = 'system'
WHERE prompt_code = 'tool_expert_executor'
  AND is_active = true;
