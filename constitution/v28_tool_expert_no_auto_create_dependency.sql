-- V28：约束 tool_expert 不得自行补建依赖主数据；缺依赖时以 done 失败 + error / need_info 结束
-- 执行：psql -U … -d aierp -f constitution/v28_tool_expert_no_auto_create_dependency.sql

UPDATE meta.prompts
SET
    content = content || E'\n\n' || $c$

## 依赖主数据（禁止越权补建）

执行用户意图时，若发现**完成当前操作所依赖的关联记录在系统中不存在**（例如：要建账号但对应员工不存在；要下单但客户/产品不存在），**不得**为了「凑齐条件」而自行再输出 `write` 去创建该条主数据或上下游依赖记录。

应**立即结束本轮**，输出 JSON（**必须**带 `"type": "done"`，且 `"success": false`）：

```json
{"type": "done", "success": false, "error": "…", "need_info": "…"}
```

- **error**：简明说明**缺什么**（缺哪张表/哪种主数据、哪项关键条件不满足）。
- **need_info**：引导用户**先去补建对应主数据**（或联系管理员），**再回来完成原始操作**；不要编造已存在的 id。

$c$,
    version = version + 1,
    updated_at = NOW(),
    updated_by = 'system'
WHERE prompt_code = 'tool_expert_executor'
  AND is_active = true;
