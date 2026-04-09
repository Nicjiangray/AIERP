-- 小王测试账号（默认密码与种子库一致：test123456）
-- 依赖：business.employees 存在 id=1；若不存在请先调整 WHERE 条件
-- 执行：psql -U nicjiang -d aierp -f constitution/v5_seed_xiaowang_account.sql

INSERT INTO business.user_accounts (employee_id, login_name, password_hash, is_active, notes)
SELECT
    1,
    'xiaowang',
    '$2b$12$Edqg39X/WnGO8YUUOUuXAutTH2XH0qkR5MCR4U6uWsyt2l.kyky96',
    true,
    '测试账号：默认密码 test123456'
WHERE EXISTS (SELECT 1 FROM business.employees e WHERE e.id = 1)
ON CONFLICT (login_name) DO UPDATE SET
    employee_id = EXCLUDED.employee_id,
    password_hash = EXCLUDED.password_hash,
    is_active = EXCLUDED.is_active,
    notes = EXCLUDED.notes,
    updated_at = NOW();
