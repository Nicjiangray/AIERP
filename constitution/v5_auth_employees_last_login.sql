-- 为员工表增加 last_login_at，供登录成功时与 user_accounts 同步更新
-- 执行：psql -U nicjiang -d aierp -f constitution/v5_auth_employees_last_login.sql

ALTER TABLE business.employees
    ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ;

COMMENT ON COLUMN business.employees.last_login_at IS '最近一次登录成功时间（与 user_accounts 同步，应用层更新）';
