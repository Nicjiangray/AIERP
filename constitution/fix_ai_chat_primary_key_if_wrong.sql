-- 若误将 ai_chat 主键建在 session_id 上，会导致每会话只能一行；正确主键应为 id。
-- 本脚本仅在 pg_get_constraintdef 显示为 PRIMARY KEY (session_id) 时修复。
-- 执行：psql -U nicjiang -d aierp -f constitution/fix_ai_chat_primary_key_if_wrong.sql

DO $$
DECLARE
    def text;
    cname name;
BEGIN
    SELECT pg_get_constraintdef(oid), conname
    INTO def, cname
    FROM pg_constraint
    WHERE conrelid = 'business.ai_chat'::regclass
      AND contype = 'p'
    LIMIT 1;

    IF def IS NULL THEN
        RAISE NOTICE 'business.ai_chat: 无主键，跳过';
        RETURN;
    END IF;

    IF def = 'PRIMARY KEY (id)' THEN
        RAISE NOTICE 'business.ai_chat: 主键已在 id 上，无需修复';
        RETURN;
    END IF;

    IF def <> 'PRIMARY KEY (session_id)' THEN
        RAISE WARNING 'business.ai_chat: 主键为「%」，非预期，请人工检查', def;
        RETURN;
    END IF;

    RAISE NOTICE 'business.ai_chat: 正在将主键从 session_id 改为 id';
    EXECUTE format('ALTER TABLE business.ai_chat DROP CONSTRAINT %I', cname);
    ALTER TABLE business.ai_chat ADD PRIMARY KEY (id);
END $$;
