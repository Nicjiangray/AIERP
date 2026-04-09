-- ============================================================
-- workflow_tasks / workflow_task_params → frames / slots
-- 新增 sessions、turns，frames 关联 session
-- ============================================================

BEGIN;

-- 第一步：改表名
ALTER TABLE business.workflow_tasks RENAME TO frames;
ALTER TABLE business.workflow_task_params RENAME TO slots;

-- slots 外键列与代码、约束统一为 frame_id
ALTER TABLE business.slots RENAME COLUMN task_id TO frame_id;

-- 第二步：frames 表加字段
ALTER TABLE business.frames
    ADD COLUMN session_id integer,
    ADD COLUMN subject varchar(200),
    ADD COLUMN context_snapshot jsonb,
    ADD COLUMN expires_at timestamp;

-- 第三步：slots 表加字段
ALTER TABLE business.slots
    ADD COLUMN value_raw varchar,
    ADD COLUMN confidence numeric(4,3),
    ADD COLUMN source varchar(20) DEFAULT 'user_input';
    -- source 取值：user_input / system_default / inferred

-- 第四步：新建 sessions 表
CREATE TABLE business.sessions (
    id serial PRIMARY KEY,
    user_id integer NOT NULL,
    started_at timestamp DEFAULT now(),
    last_active_at timestamp DEFAULT now(),
    channel varchar(20) DEFAULT 'web',
    is_active boolean DEFAULT true
);

-- 第五步：新建 turns 表
CREATE TABLE business.turns (
    id serial PRIMARY KEY,
    session_id integer NOT NULL REFERENCES business.sessions(id),
    frame_id integer REFERENCES business.frames(id),
    role varchar(10) NOT NULL, -- user / assistant
    content text NOT NULL,
    created_at timestamp DEFAULT now()
);

-- 第六步：补外键
ALTER TABLE business.frames
    ADD CONSTRAINT fk_frames_session
    FOREIGN KEY (session_id) REFERENCES business.sessions(id);

-- 第七步：加索引
CREATE INDEX idx_frames_session_id ON business.frames(session_id);
CREATE INDEX idx_frames_status ON business.frames(status);
CREATE INDEX idx_turns_session_id ON business.turns(session_id);
CREATE INDEX idx_turns_frame_id ON business.turns(frame_id);
CREATE INDEX idx_sessions_user_id ON business.sessions(user_id);

COMMIT;
