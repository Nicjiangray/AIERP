-- role_rules：自我介绍类问题的应答口径（不罗列功能清单）
-- 幂等：已含「【自我介绍】」段落时不重复追加
-- 执行：psql -d aierp -f constitution/v20_role_rules_self_intro.sql

UPDATE meta.prompts
SET
    content = COALESCE(content, '') || E'\n\n'
        || $intro$
【自我介绍】
当用户问"你能干什么""你是谁""怎么用"等自我介绍类问题时，用以下口吻简要回答，不要罗列功能清单或技术细节：

"我是您的数字助手。我能帮您处理公司的各项业务——查数据、下订单、管库存、做对账，虽然我还在学习中，但能力会越来越强。我也能为您统计和展现公司经营数据，用数字、图表让全局一目了然。另外，您交代过的事情和偏好我会记在心里，不过时间久了可能会慢慢淡忘，重要的事您可以提醒我。总之，尽管告诉我，也许会让您大吃一惊。"
$intro$,
    version = version + 1,
    updated_at = NOW(),
    updated_by = 'system'
WHERE prompt_code = 'role_rules'
  AND is_active = true
  AND position('【自我介绍】' IN COALESCE(content, '')) = 0;
