-- create_simple_entity / update_simple_entity 意图（示例句供 sync_intent_embeddings 使用）
INSERT INTO business.workflows (intent, display_name, description, is_active, example_queries)
VALUES
    (
        'create_simple_entity',
        '新建基础数据',
        '新建客户、产品或联系人等基础数据',
        1,
        '[
            "新建一个客户",
            "帮我创建客户",
            "添加产品",
            "录入联系人",
            "新建联系人",
            "创建一个产品",
            "增加客户资料",
            "添加新客户",
            "登记一个联系人",
            "新建供应商客户",
            "我要新建客户",
            "创建一条客户记录",
            "添加产品信息",
            "录入新产品",
            "新建一条联系人"
        ]'::jsonb
    ),
    (
        'update_simple_entity',
        '修改基础数据',
        '修改客户、产品、联系人等基础数据的字段值',
        1,
        '[
            "修改客户账期",
            "更新产品价格",
            "把浑圆纺织的付款方式改成TT60天",
            "联系人电话换了",
            "产品单价调整",
            "客户地址更新",
            "改一下供应商信息",
            "更新客户税号",
            "产品描述需要修改",
            "客户简称写错了",
            "修改联系人邮箱",
            "更新产品规格",
            "客户状态改为inactive",
            "修改产品单位",
            "供应商联系方式变了"
        ]'::jsonb
    )
ON CONFLICT (intent) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    description = EXCLUDED.description,
    is_active = EXCLUDED.is_active,
    example_queries = EXCLUDED.example_queries;
