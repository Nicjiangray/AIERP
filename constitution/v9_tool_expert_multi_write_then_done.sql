-- V9：在 tool_expert_executor 末尾追加「多条 write → done 汇总」说明
-- 执行：psql -U nicjiang -d aierp -f constitution/v9_tool_expert_multi_write_then_done.sql

UPDATE meta.prompts
SET
    summary = '多步 query + 多条 write 暂存后 done 汇总；单条 write 可由系统直接待确认',
    content = content || E'\n\n' || $c$

## 多条写入（与 orchestrator 对齐）

- 可连续输出多条 `type: write`；每条通过审计后会**暂存**，本轮**不会**在第一条 write 成功时结束。
- 当**全部**写入已规划完毕，请输出：`{"done": true, "success": true, "summary": "给用户看的整体说明"}`，summary 须概括将执行哪些写入（涉及表、条数、要点）。
- **仅一条**写入时，系统可能**直接进入确认**，无需你再输出 done。

$c$,
    version = version + 1,
    updated_at = NOW(),
    updated_by = 'system'
WHERE prompt_code = 'tool_expert_executor';
