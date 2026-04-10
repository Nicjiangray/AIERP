-- ai_memory：允许 scope='core'；meta.prompts：memory_core_extract
-- 执行：psql -U nicjiang -d aierp -f constitution/v14_ai_memory_scope_core_and_prompt.sql

-- 扩展 CHECK（原仅 session / longterm）
ALTER TABLE business.ai_memory DROP CONSTRAINT IF EXISTS ai_memory_scope_check;
ALTER TABLE business.ai_memory
    ADD CONSTRAINT ai_memory_scope_check
    CHECK (scope IN ('session', 'longterm', 'core'));

COMMENT ON COLUMN business.ai_memory.scope IS
    'session=单次会话要点；longterm=长期压缩；core=从 longterm 提炼的核心经验';

INSERT INTO meta.prompts (
    prompt_code,
    category,
    title,
    summary,
    content,
    sort_order,
    inject_mode,
    source,
    llm_editable,
    is_active,
    version,
    agent_role,
    parent_code,
    updated_by
)
VALUES (
    'memory_core_extract',
    'memory',
    '记忆管理员：核心经验提炼',
    '从长期记忆中提炼业务规律与操作经验，写入 scope=core',
    $prompt$
你是记忆管理员。请从以下长期记忆中，提炼出反复出现的业务规律、操作模式和关键经验。

**提取规则：**
1. 只提取出现过两次以上的模式，不提取一次性事件
2. 每条规律用一句简洁的陈述句概括，不超过50字
3. 分为以下几类：
   - **业务流程**：如"处理到货单的步骤是：匹配采购合同→更新到货数量→检查交货进度"
   - **客户/供应商特征**：如"广州锦绣的货经常短少2-3%属于正常损耗"
   - **操作经验**：如"修改客户信息前必须先查到客户id"
   - **业务规则**：如"信用额度超过10万的客户需要总经理审批"
4. 与已有的核心经验对比：
   - 已有的且仍然成立 → 保留不动
   - 已有的但需要修正 → 输出修正后的版本
   - 新发现的规律 → 追加
   - 已有的但长期记忆中不再出现相关证据 → 仍然保留（不轻易删除经验）
5. 总条数控制在 30 条以内，超出时合并相近的条目

**已有核心经验：**
{existing_core_facts}

**当前长期记忆：**
{longterm_memories}

**请输出更新后的完整核心经验列表，每条一行，格式为：**
【类别】内容
$prompt$,
    25,
    'on_demand',
    'manual',
    FALSE,
    TRUE,
    1,
    'memory_manager',
    NULL,
    'system'
)
ON CONFLICT (prompt_code) DO UPDATE SET
    category = EXCLUDED.category,
    title = EXCLUDED.title,
    summary = EXCLUDED.summary,
    content = EXCLUDED.content,
    sort_order = EXCLUDED.sort_order,
    inject_mode = EXCLUDED.inject_mode,
    source = EXCLUDED.source,
    llm_editable = EXCLUDED.llm_editable,
    is_active = EXCLUDED.is_active,
    version = EXCLUDED.version,
    agent_role = EXCLUDED.agent_role,
    parent_code = EXCLUDED.parent_code,
    updated_at = NOW(),
    updated_by = EXCLUDED.updated_by;
