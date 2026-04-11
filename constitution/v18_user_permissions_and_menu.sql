-- 个人表权限覆盖 + 主管链（manager_id）；执行后 expert 可配置 user_permissions
-- psql -d aierp -f constitution/v18_user_permissions_and_menu.sql

BEGIN;

-- ---------------------------------------------------------------------------
-- 1) business.user_permissions
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS business.user_permissions (
    id SERIAL PRIMARY KEY,
    employee_id INT NOT NULL REFERENCES business.employees(id),
    table_name VARCHAR(100) NOT NULL,
    can_read BOOLEAN NOT NULL DEFAULT true,
    can_write BOOLEAN NOT NULL DEFAULT false,
    can_delete BOOLEAN NOT NULL DEFAULT false,
    granted_by INT REFERENCES business.employees(id),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    deleted_at TIMESTAMPTZ,
    UNIQUE(employee_id, table_name)
);

COMMENT ON TABLE business.user_permissions IS '个人级权限覆盖：优先于角色默认权限，由管理员通过 AI 对话等方式配置';

CREATE INDEX IF NOT EXISTS idx_user_permissions_employee
    ON business.user_permissions (employee_id) WHERE deleted_at IS NULL;

-- ---------------------------------------------------------------------------
-- 2) 主管关系（方案约定：下属 employees.manager_id = 主管 id）
-- ---------------------------------------------------------------------------
ALTER TABLE business.employees
    ADD COLUMN IF NOT EXISTS manager_id INT REFERENCES business.employees(id) ON DELETE SET NULL;

COMMENT ON COLUMN business.employees.manager_id IS '直属上级（员工 id）；用于主管数据可见范围说明';

COMMIT;

CALL meta.sync_constitution();

-- ---------------------------------------------------------------------------
-- 3) assistant：tool_write_interface 补充「人员权限」意图示例
-- ---------------------------------------------------------------------------
UPDATE meta.prompts
SET
    content = content || E'\n\n'
        || $frag$- 人员权限配置：设置员工角色、调整个人表权限（加权/减权，表 business.user_permissions）、设置主管关系（employees.manager_id）
  示例：
  [TOOL]
  action: write
  intent: 给小王加上库存表的读权限
  [/TOOL]
$frag$,
    version = version + 1,
    updated_at = NOW(),
    updated_by = 'system'
WHERE prompt_code = 'tool_write_interface'
  AND is_active = true;
