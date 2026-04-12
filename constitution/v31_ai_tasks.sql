-- ============================================================
-- V31：任务状态机表
-- 用途：持久化多步任务的计划与执行状态，由 orchestrator 代码全权管理。
-- 注意：include_in_domain_model = false，tool_expert 不可见此表。
-- ============================================================

CREATE TABLE IF NOT EXISTS business.ai_tasks (
    id              SERIAL PRIMARY KEY,

    -- 关联会话（任务生命周期不跨 session，session 结束任务自然沉底）
    session_id      INTEGER REFERENCES business.ai_sessions(id),

    -- 发起任务的用户
    user_id         INTEGER NOT NULL,

    -- 任务整体状态
    -- planning : 主助手已输出 [PLAN]，尚未开始执行
    -- running  : 至少一步已开始执行
    -- completed: 所有步骤执行成功，final_summary 已写入
    -- failed   : 步骤失败且超出重规划上限，或主助手主动终止
    -- cancelled: 预留（当前版本暂不使用）
    status          TEXT NOT NULL DEFAULT 'planning'
                    CHECK (status IN ('planning','running','completed','failed','cancelled')),

    -- 用户的原始意图，完整文本，供主助手汇总时引用
    original_intent TEXT NOT NULL,

    -- 步骤数组，结构如下：
    -- [
    --   {
    --     "step"   : 1,          -- 1-based 步骤序号，展示用
    --     "intent" : "创建员工张圣忠，部门设计部",  -- 步骤意图，自然语言
    --     "details": "",         -- orchestrator 注入上一步 returning 结果（如 employee_id=11）
    --     "status" : "pending",  -- pending / running / completed / failed
    --     "result" : null        -- confirm 后写入 returning 数据或错误原因
    --   },
    --   ...
    -- ]
    plan            JSONB NOT NULL,

    -- 当前正在执行的步骤下标（0-based，对应 plan 数组下标）
    current_step    INTEGER NOT NULL DEFAULT 0,

    -- 重新规划次数，超过 max_replans 后强制 status=failed
    replan_count    INTEGER NOT NULL DEFAULT 0,
    max_replans     INTEGER NOT NULL DEFAULT 2,

    -- 全部完成后由主助手写入的总结文本
    final_summary   TEXT,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 查询当前 session 活跃任务的索引
CREATE INDEX IF NOT EXISTS idx_ai_tasks_session_status
    ON business.ai_tasks(session_id, status);

-- 表与字段注释
COMMENT ON TABLE business.ai_tasks IS
    '多步任务状态机。每条记录对应一次多步操作（如先建员工再建账号）。'
    '由 orchestrator 代码全权读写，LLM 通过注入的 [TASK_STATUS] 文本感知状态，不直接访问此表。';

COMMENT ON COLUMN business.ai_tasks.session_id      IS '关联会话。任务不跨 session，session 结束后任务自然沉底。';
COMMENT ON COLUMN business.ai_tasks.user_id         IS '发起任务的用户 id（对应 business.employees.id）。';
COMMENT ON COLUMN business.ai_tasks.status          IS '任务整体状态：planning/running/completed/failed/cancelled。';
COMMENT ON COLUMN business.ai_tasks.original_intent IS '用户的原始意图完整文本，供主助手汇总时引用。';
COMMENT ON COLUMN business.ai_tasks.plan            IS '步骤数组（JSONB）。每步含 step/intent/details/status/result。'
                                                       'details 由 orchestrator 在执行前注入上一步 returning 数据。'
                                                       'result 由 orchestrator 在 confirm 后写入返回值或错误原因。';
COMMENT ON COLUMN business.ai_tasks.current_step    IS '当前执行步骤的 0-based 下标，对应 plan 数组位置。';
COMMENT ON COLUMN business.ai_tasks.replan_count    IS '已重新规划次数。超过 max_replans 后强制 status=failed。';
COMMENT ON COLUMN business.ai_tasks.max_replans     IS '允许重新规划的最大次数，默认 2。';
COMMENT ON COLUMN business.ai_tasks.final_summary   IS '所有步骤完成后，由主助手写入的汇总说明。';

-- 确保新表进入元数据目录，但标记为不注入 tool_expert
-- （meta.meta_tables 无 schema 列，以 table_name 唯一标识；需先 sync 才有 ai_tasks 行）
CALL meta.sync_constitution();

UPDATE meta.meta_tables
SET    include_in_domain_model = false
WHERE  table_name = 'ai_tasks';
