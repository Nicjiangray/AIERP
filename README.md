# AI·ERP（aierp）

数据库驱动的 ERP 辅助：FastAPI 后端 + Vue 前端，对话与写入走 PostgreSQL 元数据（constitution）与业务表。

## 版本进度

### V4：记忆系统 + Agent 分工（2026-04-09）

**记忆架构**

- 三层记忆：session 级要点（每次清洗）→ 当日短期记忆（注入时实时拼）→ longterm 长期记忆（压缩任务）
- 存储表：`business.ai_memory`（`scope`：`session` / `longterm`）
- 清洗：调 LLM（主配置为 DeepSeek 时走 `LLM_*`；本机 Ollama 主模型时记忆任务走 `FALLBACK_*`）从 `ai_chat` 提取陈述性要点，写入 `ai_memory(scope=session)`
- 压缩：当日 session 要点 + 旧 longterm → LLM 压缩 → 覆盖 longterm（提示词中约定约 3000 字封顶）
- 注入：`build_prompt()` 拼装时从 `ai_memory` 拉长期记忆 + 今日 session 要点，注入主助手 system prompt

**Agent 分工**

- **assistant**（主助手）：面向用户对话，注入人格、工具接口、记忆（长期记忆中可含「业务规律」小节）；只知工具调用格式，**不**注入表结构（`schema_shadow` 归 tool_expert）
- **tool_expert**（工具专家）：注入表结构 + 工具执行细节；**V5** 起主路径为 **orchestrator 截获 `[TOOL]` → tool_expert（多步 SQL）**（见下方 **V5**）
- **memory_manager**（记忆管理员）：后台逻辑，负责 session 清洗与 longterm 压缩；对应 prompt 为 `on_demand` 拉取
- **auditor**（审计员）：未实现，规划中

**新增文件**

- `engine/prompt_builder.py`：统一 prompt 拼装 `build_prompt()` / `build_prompt_on_demand()`，按 `agent_role` 从 `meta.prompts` 取静态片段，按 `user_id` 从 `ai_memory` 取动态记忆
- `engine/memory_manager.py`：`clean_session()` 会话清洗 + `compress_longterm()` 长期压缩

**改造**

- `/ai/chat`：用 `build_prompt('assistant', user_id=...)` 替换硬编码 `CHAT_SYSTEM`；新增会话持久化（自动创建 `ai_sessions`、写入 `ai_chat`，需有效 `user_id` 且存在于 `business.employees`）
- `meta.prompts`：新增 `agent_role`；`tool_query` / `tool_write` 拆为接口描述（assistant，`tool_*_interface`）与执行细节（tool_expert）
- `role_rules`：区分「业务数据不可编造」与「对话记忆可参考」，消除与记忆注入的冲突

**新增 API**

- `POST /ai/memory/clean-session`：手动触发 session 清洗
- `POST /ai/memory/compress-longterm`：手动触发 longterm 压缩

**未完成（留待后续版本）**

- 凌晨 cron 自动压缩
- 新 session 开启时自动清洗上一 session
- 业务规律提炼并写入 `meta.prompts`（如 `category=business_rule`）
- 前端展示记忆与业务规律

> 对话主路径与 SQL 执行方式的演进见 **V5**（统一 `sql_engine`、orchestrator + tool_expert）。

---

### V5：统一 SQL 引擎 + Orchestrator + 登录（2026-04-09）

**统一 SQL 引擎（`engine/sql_engine.py`）**

- 废弃「结构化 payload → query_engine / write_engine」的主路径；**tool_expert 直接产出可审计的 SQL**（+ `params`），由引擎统一处理。
- 依赖 **sqlparse** 等做安全审计：白名单表（`include_in_domain_model`）、禁止危险关键字与整表写等。
- **`execute_sql`**：审核并执行 **SELECT**（含参数化），返回行集摘要。
- **`preview_sql`**：审核写 SQL，生成中文摘要等，供用户确认（不提交事务）。
- **`confirm_execute_sql`**：用户确认后执行经预览同一套 SQL（`/ai/confirm` 走此入口）。

**assistant → orchestrator → tool_expert**

- 主助手在回复中输出 **`[TOOL] ... [/TOOL]`** 意图块（自然语言键值对：**不含**表名、字段名），仅表达「要查什么 / 改什么」。
- **`main.py` 中 orchestrator** 解析该块，将内容作为 user 消息交给 **tool_expert**（`build_prompt('tool_expert')`，含 `schema_shadow` + `tool_expert_executor`）。
- **tool_expert** 内部多步闭环（最多 **5** 步）：每步输出一个 JSON（`type: query` 调 `execute_sql`，`type: write` 调 `preview_sql`），系统将执行结果贴回对话，直到 `done` 或预览成功；结果以 **`[TOOL_RESULT] ... [/TOOL_RESULT]`** 回注主助手下一轮。
- **职责**：助手负责业务规划（对用户说话、收信息），**无表结构**；expert 负责操作规划（写 SQL、查 id），**无用户对话上下文**。

**登录系统**

- **`POST /auth/login`**：`business.user_accounts` + **bcrypt** 校验密码；成功返回 `user`（`id` 为 **`employees.id`**）；可选更新 `last_login_at`（见迁移 `v5_auth_employees_last_login.sql`）。
- **`GET /auth/me?user_id=`**：按员工 id 拉用户信息，供前端刷新恢复。
- **前端**：`LoginView.vue` + `stores/auth.js`（`localStorage` 键 `aierp_user_id`）；`App.vue` 未登录仅渲染登录页；**`AiPanel` 请求 `/ai/chat` 等的 `user_id` 取自登录态**（无硬编码默认用户 id）。

**Prompt 变更（`meta.prompts`）**

- **`tool_query_interface` / `tool_write_interface`**：重写为**意图级菜单**格式（约定 `[TOOL]` 块，不出现表字段名）。
- **新增 `tool_expert_executor`**（`agent_role=tool_expert`，`inject_mode=always`）：教 expert 写 **SQL + params**、多步规划与 `done` 结束条件。
- **`tool_query` / `tool_write`**：**停用**（`is_active=false`），由 SQL 路径与 `tool_expert_executor` 取代。

**相关迁移（节选）**

- `v5_tool_expert_executor_and_assistant_tool_prompts.sql`、`v5_2_sql_engine_tool_expert.sql`、`v5_2_tool_expert_multistep.sql`
- `v5_auth_employees_last_login.sql`、`v5_seed_xiaowang_account.sql`（测试账号，可选）

**未完成（V5 之后）**

- 端到端测试：受控查询、写入预览与确认、多轮 tool_expert、登录与会话持久化同一条 `user_id`。
- 凌晨 cron、新 session 清洗、业务规律提炼、前端展示记忆（与 V4 清单衔接，仍待实现）。

---

## 项目目录结构概览

```
aierp/
├── main.py                 # FastAPI：表 CRUD、/auth/*、/ai/chat（orchestrator）、/ai/memory/*、/ai/confirm
├── engine/                 # sql_engine、prompt_builder、memory_manager；query/write_engine 已 deprecated
├── llm/                    # OpenAI 兼容客户端（chat / 流式 / 附件解析）；intent 为占位
├── tools/                  # 连接池、通用 DB 读写与会话 memo 等辅助
├── scripts/                # sync_meta_pipeline、sync_meta_cn、sync_schema_shadow
├── constitution/           # SQL 迁移与种子（表结构、prompts、ai_memory 等）
├── frontend/               # Vue 3 + Vite（入口见下方说明）
│   ├── index.html          # 挂载 /src/main.js
│   └── src/                # 实际使用的页面与组件（见「前端现状」）
├── dev.sh                  # 一键前后端
├── requirements.txt
├── README.md
└── 常用命令.txt
```

`frontend/` 根目录下另有若干与 `frontend/src/` **同名或重复的 .vue / main.js**（历史拷贝），**以 `frontend/src/` 与 `index.html` 引用的 `/src/main.js` 为准**。

---

本文档用于**快速回忆**：日常命令、迁移顺序、代码与接口现状。更细的命令备忘见 [`常用命令.txt`](./常用命令.txt)。

---

## 后端代码现状（`engine/`）

以下为 **`engine/` 目录下各 `.py` 文件**说明。

| 文件 | 用途简述 | 当前状态 |
|------|----------|----------|
| `__init__.py` | 包说明：统一入口为 **`sql_engine`**；`query_engine` / `write_engine` 为历史路径 | 已完成 |
| **`sql_engine.py`** | **统一 SQL 引擎**：`execute_sql`（SELECT）、`preview_sql`（写预览）、`confirm_execute_sql`（确认执行）；**sqlparse** 审计 + 白名单表 | **主路径**：由 **tool_expert**（经 orchestrator）与 **`/ai/confirm`** 调用 |
| `query_engine.py` | 旧版：结构化 payload + 白名单的受控 SELECT（`execute_query`） | **deprecated**：不再被 `/ai/chat` 主链路使用；保留文件供对照或迁移 |
| `write_engine.py` | 旧版：结构化 payload 的预览与写入 | **deprecated**：同上 |
| `prompt_builder.py` | 统一 prompt 拼装：按 `agent_role` 取 `meta.prompts` + 按 `user_id` 取 `ai_memory` | **已完成**：`assistant` / `tool_expert` / `memory_manager` |
| `memory_manager.py` | 记忆管理员：`clean_session()`、`compress_longterm()` | **已完成**：手动 API 触发 |

---

## API 路由现状（`main.py`）

路由均定义在 **`main.py` 顶层 `app`**，无独立 `router` 文件。

| 路径 | 方法 | 用途 | 当前状态 |
|------|------|------|----------|
| `/` | GET | 健康探测 JSON | 可用 |
| `/health` | GET | 健康检查 | 可用 |
| `/tables` | GET | 列出 `meta` / `business` / `ai_assistant` 下基表 | 可用 |
| `/nav/tables` | GET | 按 `meta.meta_tables` 的 `nav_section` 返回设计/预备/运行导航 | 可用 |
| `/table-columns/{table_name}` | GET | 从 `meta.meta_fields` 取列与菜单标签 | 可用 |
| `/table/{schema}/{table_name}` | GET | 分页查询表数据（筛选、排序、`q` 等） | 可用 |
| `/export/xlsx/{schema}/{table_name}` | GET | 导出 xlsx（依赖 `openpyxl`） | 可用；未装库时 503 |
| `/table/{schema}/{table_name}` | POST | 按可写字段插入一行 | 可用 |
| `/table/{schema}/{table_name}/{row_id}` | PATCH | 按 `id` 更新 | 可用（需表有整数 `id`） |
| `/table/{schema}/{table_name}/{row_id}` | DELETE | 硬删除一行 | 可用 |
| **`/auth/login`** | **POST** | **body：`{ login_name, password }`**；bcrypt 校验 **`business.user_accounts`**；返回 **`user`**（`id`=`employees.id`） | 可用 |
| **`/auth/me`** | **GET** | **Query：`user_id`**（员工 id）；返回 **`{ user: {...} }`**，不存在则 404 | 可用 |
| `/ai/confirm` | POST | 执行经 **`preview_sql`** 审核的写 SQL：**`payload` 须含 `sql`、可选 `params`（数组）** → `confirm_execute_sql` | 可用 |
| `/ai/chat` | POST | **orchestrator**：`build_prompt('assistant', user_id)` + 流式/非流式 LLM；若回复含 **`[TOOL]`** 则调用 **tool_expert**（多步 `execute_sql` / `preview_sql`），以 **`[TOOL_RESULT]`** 回注；**SSE** 默认；传 **`user_id`** 时自动建 **`ai_sessions`** 并写 **`ai_chat`** | 可用 |
| `/ai/memory/clean-session` | POST | 手动触发 session 清洗，要点写入 `business.ai_memory`（`scope=session`） | 可用（body：`{"session_id": <int>}`） |
| `/ai/memory/compress-longterm` | POST | 手动触发长期记忆压缩，覆盖/写入 `scope=longterm` | 可用（body：`{"user_id": <int>}`） |
| `/ai/chat`（`pendingToken`） | POST | — | **未联通**：显式返回 400，提示不支持恢复 |
| `/ai/chat`（`confirm: true`） | POST | — | **未联通**：显式返回 400，提示不在此确认 DB |

`user_id` 须能关联 **`business.employees`**，否则插入 `ai_sessions` 会违反外键。

---

## 前端现状（`frontend/src/`）

入口：`index.html` → `/src/main.js` → `App.vue` + `router` + Pinia。

| 路径/文件 | 用途 | 当前状态 |
|-----------|------|----------|
| `App.vue` | **`n-config-provider` 包裹**；**未登录**仅 **`LoginView`**；**已登录**为 `NavPanel` + `RouterView` + `AiPanel`；`onMounted` 调用 **`auth.restoreSession()`** | 可用 |
| **`views/LoginView.vue`** | **登录页**：登录名 + 密码 → **`POST /auth/login`** | 可用 |
| **`stores/auth.js`** | **`currentUser`**、`login` / `logout` / **`restoreSession`**（`localStorage`：`aierp_user_id`） | 可用 |
| `router/index.js` | `/dashboard`、`/meta`、`/business`、`/meta/:table`、`/business/:table`、`/ai-rules` | 可用（登录页不走路由，由 `App.vue` 条件渲染） |
| `stores/app.js` | 请求 `http://localhost:8000` 的 `/tables`、`/nav/tables`、`/table-columns`、`/table/...`、导出 URL、表 CRUD | 可用 |
| `components/NavPanel.vue` | 侧栏导航；**展示当前用户名 + 退出** | 可用 |
| `components/DataNavPanel.vue` | 数据侧导航（与表拆分配置配合） | 可用 |
| `components/AiPanel.vue` | AI 对话：SSE **`/ai/chat`**、**`/ai/confirm`**；请求体 **`user_id` 取自 `useAuthStore().currentUser?.id`**（与登录态一致） | 可用 |
| `views/DashboardView.vue` | 仪表盘占位 | 可用 |
| `views/MetaView.vue` / `BusinessView.vue` | 元数据/业务表列表入口 | 可用 |
| `views/TableView.vue` | 通用表浏览与编辑 | 可用 |
| `views/AiRulesView.vue` | 展示 `meta.ai_rules` 表数据 | 可用 |
| `config/tableSplit.js` | 表拆分配置 | 可用 |
| `utils/tableExport.js` | 导出 xlsx URL 构造 | 可用 |

---

## 已联通 vs 未联通（总结）

- **表数据与导航**：前端 `stores/app.js` ↔ 后端表 API（`/tables`、`/nav/tables`、`/table/...`、导出）**已串通**。
- **登录**：`LoginView` → **`/auth/login`**；刷新 **`/auth/me`**；**`AiPanel` 携带同一 `user_id`** → **已实现（待测试）**。
- **对话主链路**：用户输入 → `AiPanel` → `POST /ai/chat`（SSE）→ **`build_prompt('assistant')`** + LLM；若含 **`[TOOL]`** → **orchestrator** → **tool_expert**（多步 **`sql_engine`**）→ **`[TOOL_RESULT]`** 回注 → **已实现（待测试）**。
- **查询 / 写入（经 tool_expert）**：自然语言意图 → **`[TOOL]`** → expert 产出 **SQL** → `execute_sql` / `preview_sql` → 前端确认 → **`/ai/confirm`**（`sql`+`params`）→ **`confirm_execute_sql`** → **已实现（待测试）**（旧版 `query_engine.execute_query` 与结构化 `preview_write` 主路径已弃用）。
- **记忆系统**：**已串联（手动触发）**——`clean-session` / `compress-longterm` 写入 `business.ai_memory`；主助手注入长期 + 今日 session 要点；**自动**定时清洗/压缩 **仍待实现**。
- **会话持久化**：**已联通**——提供有效 **`user_id`**（登录态）时自动建 **`ai_sessions`** 并写 **`ai_chat`**（须满足 `employees` 外键）。

---

## 环境与启动

- **Python**：项目根目录使用虚拟环境 `.venv`（`source .venv/bin/activate`）。
- **依赖（V5 相关）**：`bcrypt`（登录）、`sqlparse`（`sql_engine` 审计），见根目录 **`requirements.txt`**。
- **数据库**：默认 `psql -U nicjiang -d aierp`（可用 `PGUSER` / `PGDATABASE` 覆盖）。
- **一键前后端**：`./dev.sh`（根目录执行；否则可能加载不到本仓库的 `main.py`）。
- **仅后端**：`.venv/bin/python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000`
- **仅前端**：`cd frontend && npm run dev`
- **端口占用**（8000）：`lsof -nP -iTCP:8000 -sTCP:LISTEN` → `kill <PID>`

---

## 元数据流水线（推荐记这一条）

顺序与数据依赖一致：

1. `CALL meta.sync_constitution()` — 从 `information_schema` 重建 `meta_tables` / `meta_fields`
2. `scripts/sync_meta_cn.py` — 为空 `field_cn` 批量填中文（依赖 `.env` 里 `LLM_*`）
3. `scripts/sync_schema_shadow.py` — 按 `include_in_domain_model` 裁剪后写入 `meta.prompts`（`prompt_code = schema_shadow`）

一键（根目录）：

```bash
.venv/bin/python scripts/sync_meta_pipeline.py
```

等价于「宪法 + 中文名」的旧脚本若仍存在，见 [`常用命令.txt`](./常用命令.txt)。

常用参数：

- `--skip-meta-cn`：不跑 LLM 填中文
- `--only-schema-shadow`：只重写 `schema_shadow` 那条 prompt

---

## Constitution SQL（`constitution/`）

以下按**常见依赖顺序**排列；新环境或换库时按序执行缺失项即可（具体以各文件头注释为准）。

| 文件 | 作用 |
|------|------|
| `sync_constitution.sql` | 定义/注册 `meta.sync_constitution()` 等（若库中尚无） |
| `create_meta_prompts.sql` | `meta.prompts` 提示词表（含 `agent_role`） |
| `add_meta_prompts_agent_role.sql` | 旧库：补 `agent_role` + 记忆管理员两条 prompt（新库若已用 `create_meta_prompts.sql` 可跳过） |
| `create_ai_sessions_and_ai_chat.sql` | `business.ai_sessions` / `business.ai_chat` |
| `create_business_ai_memory.sql` | `business.ai_memory`（会话要点 / 长期记忆） |
| `add_meta_tables_include_in_domain_model.sql` | 领域模型裁剪相关列（若使用 schema_shadow） |
| `seed_prompts_role_rules.sql` | 种子：`role_rules`（含记忆与业务数据措辞区分） |
| `seed_prompt_tool_query.sql` / `seed_prompt_tool_write.sql` | 历史种子：`tool_query` / `tool_write`（**V5 迁移中可停用**；新环境仍可能先执行再跑 `v5_2_tool_expert_multistep.sql`） |
| `update_meta_prompts_tool_expert_and_interfaces.sql` | tool_expert 归属 + 主助手 `tool_*_interface` |
| **`v5_tool_expert_executor_and_assistant_tool_prompts.sql`** | **V5**：新增 **`tool_expert_executor`**；重写 **`tool_query_interface` / `tool_write_interface`**（意图级、`[TOOL]`） |
| **`v5_2_sql_engine_tool_expert.sql`** | **V5.2**：`tool_expert_executor` 改为 **SQL + params** 单步说明（与 `sql_engine` 对齐） |
| **`v5_2_tool_expert_multistep.sql`** | **V5.2**：`tool_expert_executor` 最终版——**多步 query/write + `done`**；**停用 `tool_query` / `tool_write`**（`is_active=false`） |
| **`v5_auth_employees_last_login.sql`** | **V5**：`business.employees.last_login_at`（登录成功时可选更新） |
| **`v5_seed_xiaowang_account.sql`** | **V5**：测试账号种子（可选） |
| `update_role_rules_memory_prompt.sql` | 更新 `role_rules` 全文（记忆与数据规则） |
| `alter_ai_sessions_status_default.sql` | `ai_sessions.status` 默认 `in_progress` |
| `fix_ai_chat_primary_key_if_wrong.sql` | 若误将 `ai_chat` 主键建在 `session_id` 上则修复为 `id`（正常库为 no-op） |
| `drop_business_feedback.sql` / `drop_business_sessions_frames_slots_turns.sql` / `nav_workflow_contract_items.sql` | 按需：删旧表或导航种子（见各文件头注释） |

执行示例：

```bash
psql -U nicjiang -d aierp -f constitution/create_meta_prompts.sql
```

**改表或种子后**：再跑一次 `CALL meta.sync_constitution();`（若需把新表同步进 `meta_*` 目录）。

---

## 容易混的表（心智模型）

- **`meta.prompts`**：所有注入 LLM 的提示词模块；字段 **`agent_role`** 区分归属。`inject_mode`：`always` = 拼装进 `build_prompt(agent_role)`，`on_demand` = 仅 `build_prompt_on_demand(prompt_code)`（如记忆清洗/压缩脚本）。
- **`business.ai_memory`**：用户级 AI 记忆（`scope`：`session` / `longterm`），`source_session_id` 可选指向 **`business.ai_sessions`**。
- **`business.ai_sessions` / `business.ai_chat`**：对话会话与消息原文；持久化由 `/ai/chat` 在提供 `user_id` 时写入。

**`agent_role` 与典型 prompt 条目（V5 之后）**

| `prompt_code` | `agent_role` | 说明 |
|---------------|----------------|------|
| `role_rules` | assistant | 人格与行为规则 |
| `tool_query_interface` | assistant | **V5 重写**：意图级菜单，约定 **`[TOOL]`**（自然语言意图，**不含**表名/字段名） |
| `tool_write_interface` | assistant | **V5 重写**：同上 |
| `schema_shadow` | tool_expert | 领域表结构 JSON（`include_in_domain_model` 裁剪） |
| **`tool_expert_executor`** | **tool_expert** | **V5 新增**：教 expert 输出 **`type: query|write`** 的 JSON + 多步闭环 + **`done`**；与 **`sql_engine`** 执行链路一致 |
| `tool_query` | tool_expert | **V5 停用**（`is_active=false`）：旧版结构化 query 说明，由 SQL 路径取代 |
| `tool_write` | tool_expert | **V5 停用**（`is_active=false`）：旧版结构化 write 说明，由 SQL 路径取代 |
| `memory_session_clean` | memory_manager | 会话清洗（on_demand） |
| `memory_longterm_compress` | memory_manager | 长期压缩（on_demand） |

> **`build_prompt('tool_expert')`** 只加载 **`is_active = TRUE`** 且 **`inject_mode = 'always'`** 的片段，因此停用的 **`tool_query` / `tool_write`** 不会进入 expert 的 system prompt；典型注入组合为 **`schema_shadow` + `tool_expert_executor`**。

---

## 其他脚本（按需）

- `scripts/sync_schema_shadow.py`：单独生成 schema 影子 JSON 到 `meta.prompts`
- `scripts/sync_meta_cn.py`：单独补字段中文名
