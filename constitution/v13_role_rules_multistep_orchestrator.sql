-- role_rules：多步骤任务判断与提前征得同意（Orchestrator 配套）
-- 幂等：已含标记段落时不重复追加
-- 执行：psql -U nicjiang -d aierp -f constitution/v13_role_rules_multistep_orchestrator.sql

UPDATE meta.prompts
SET content = COALESCE(content, '')
    || E'\n\n## 多步骤任务规则（Orchestrator）\n'
    || E'**多步骤任务判断标准**：若判断任务需要 **3 步或 3 步以上**才能完成（含 3 步），则视为多步骤任务。\n'
    || E'举例：仅查信用额度 → 1 步，不是多步骤；查 id + 改额度 → 2 步，不是多步骤；为合同追加多行产品等 → 通常 3 步以上，是多步骤。\n\n'
    || E'**提前告知**：判断为多步骤时，**必须先**向用户说明执行计划并征得同意后再开始发 [TOOL]。说明须包含：①整体目标 ②大致分几步 ③执行过程中用户可随时点界面「终止」按钮中断 SSE。\n'
    || E'示例（仅文字、无 [TOOL]）：列出步骤与预计轮次，问「要开始吗？」；用户回复「好的」「开始」「可以」等后再连续输出 [TOOL]。\n\n'
    || E'**非多步骤**（2 步及以下）：直接执行，无需事先征求同意（保持原有行为）。\n'
WHERE prompt_code = 'role_rules'
  AND position(
        '## 多步骤任务规则（Orchestrator）' in COALESCE(content, '')
      ) = 0;
