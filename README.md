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
- **tool_expert**（工具专家）：由 orchestrator 在解析到 **`[TOOL]`** 后调用 **`_call_tool_expert`**，注入表结构 + `tool_expert_executor`；与主助手通过 **`[TOOL_RESULT]`** 闭环（见 **V5** 双层说明，非「单段对话直连接 expert」）
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
- 业务规律提炼并写入 `meta.prompts`（如 `category=business_rule`）

**已实现（会话衔接）**：登录用户每次 `POST /ai/chat` 且带有 `session_id` 时，若上一会话有 `ai_chat` 但未写入 session 要点，会先同步 `clean_session` 一次再拼 `build_prompt`（见 `memory_manager.maybe_clean_previous_session`）。
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

**assistant → orchestrator → tool_expert（当前实现）**

链路分**两层**，请勿与旧文档混淆：

1. **外层：`/ai/chat` orchestrator（`main.py`）**  
   - 最多 **`MAX_ORCHESTRATOR_ROUNDS`（当前 30）** 轮「主助手 ↔ 工具结果」循环；**`MAX_TOOL_ROUNDS`** 为其**历史别名**，与内层 **`MAX_EXPERT_STEPS`（当前 30）** 不是同一概念（见下）。  
   - 每轮：主模型生成完整回复 → 解析是否含 **`[TOOL] ... [/TOOL]`**（意图块，自然语言键值对，**不含**表名/字段名）。  
   - **若无 `[TOOL]`**：本请求结束。  
   - **若有 `[TOOL]`**：SSE 对用户**只展示** `[TOOL]` **之前**的正文（块内与 `[/TOOL]` 之后续写均不下发）；再下发「请稍等......」→ 调用内层 **`_call_tool_expert`** → 将 **`[TOOL_RESULT] ... [/TOOL_RESULT]`** 作为 **user** 消息贴回 `work_msgs`，进入下一轮主模型（外层最多 30 轮；另有空转检测与客户端断开终止，见 `main.py`）。  
   - **主模型选型**：默认 **`chat` / `chat_stream`**（常规主助手）；请求体带 **`inviteExpert: true`** 时，主模型改为 **`chat_expert_only`**（仍走同一 `[TOOL]` 与 orchestrator 逻辑，仅换主模型）。

2. **内层：`_call_tool_expert`（tool_expert 专用会话）**  
   - System：`build_prompt('tool_expert')`（**`schema_shadow` + `tool_expert_executor`** 等）。  
   - User：外层解析出的 **`[TOOL]` 块内容**（外加 orchestrator 注入的说明），**不是**把整段主助手对话交给 expert。  
   - Expert 在**独立多轮 JSON** 中执行：`type: query` → **`execute_sql`**，`type: write` → **`preview_sql`**，直至 `done` 或单条/多条写入预览就绪；最多 **`MAX_EXPERT_STEPS`（当前 30）** 步（适合多行合同、多 `write` 再 `done` 汇总）。  
   - 返回给外层的是结构化结果；成功 **`done`** 时结果中含 **`type: "done"`** 与 **`success: true`**。由 **`_format_tool_result`** 包成 **`[TOOL_RESULT]`** 回注主助手（**不**在此处拼任务进度：专家 **`done`+`success`** 仅表示预览闭环，写入尚未发生）。多步任务进度只在用户 **`/ai/confirm`** 确认写入后，在响应字段 **`tool_result`** 里注入（见下文 **V9**）。

3. **写入确认与重试**  
   - 预览成功 → 前端 **`/ai/confirm`** 执行 **`confirm_batch_sql`** 等；执行/意图校验失败时由 **`_call_tool_expert_recover_after_failed_confirm`** 再跑内层 expert，最多 **`MAX_WRITE_CONFIRM_ATTEMPTS`（当前 3）** 次，与 **`MAX_EXPERT_STEPS`**（内层 expert 步数）、**`MAX_ORCHESTRATOR_ROUNDS`**（外层轮数）无关。

4. **职责划分**：主助手面向用户与 **`[TOOL]` 意图**；**tool_expert** 面向 **SQL + `params`** 与库表结构，通过 orchestrator 与 **`sql_engine`** 对接。

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

### V6：业务表扩展 + Orchestrator 多步（2026-04）

**业务表（`business` schema，迁移见 `constitution/`）**

- **采购**：`suppliers`（供应商）、`purchase_orders`、`purchase_order_items`（含 `delivered_qty`、`source_contract_item_id` 等）  
- **物流**：`receiving_notes`、`receiving_note_items`（到货）；`shipping_notes`、`shipping_note_items`（发货；明细可关联 `inventory_id`）  
- **收付款与库存**：`payments`、`payment_allocations`；`inventory`（按产品/批次等）  
- **字段修订**：`contract_items.expected_delivery_date`、`purchase_order_items.expected_delivery_date`（交期仅子表；主表交期列已由 **`v36_drop_main_table_delivery_columns.sql`** 删除）；`contract_items.shipped_qty`；`contracts.received_amount`  

**Orchestrator**

- 外层循环上限：**`MAX_ORCHESTRATOR_ROUNDS = 30`**（**`MAX_TOOL_ROUNDS`** 为同义旧名）；内层单次 **`[TOOL]`** 内 expert 仍由 **`MAX_EXPERT_STEPS`** 约束，二者独立。  
- 连续相同 `(action, intent)` 空转检测、跑满轮数提示、SSE 断开时写入系统消息；**`role_rules`** 追加多步骤任务说明（迁移 **`v13_role_rules_multistep_orchestrator.sql`**）。

**记忆（`ai_memory`）**

- 第三层 **`scope=core`**（核心经验）：由 **`memory_core_extract`** 在 **`compress_longterm`** 完成后自动提炼，或 **`POST /ai/memory/extract-core`** 手动触发；主助手注入顺序为 **core → longterm → session（近期要点）**，超长时先裁 session、再裁 longterm，**core 不裁**（见 `MEMORY_INJECT_MAX_CHARS`）。  
- 前端 **`/memory`** 可查看/编辑 **`core`** 与 **`longterm`**（不展示 session）。

**相关迁移（节选）**

| 文件 | 作用 |
|------|------|
| **`v10_purchasing_suppliers_po.sql`** | 供应商 + 采购订单 + 采购明细 |
| **`nav_purchasing_tables.sql`** | `meta.meta_tables` 导航：采购主从表等 |
| **`v11_receiving_shipping_tables.sql`** | 到货单/发货单四表 + `contract_items.shipped_qty` |
| **`v12_payments_inventory_delivery_fix.sql`** | 收付款、分配、库存、`shipping_note_items.inventory_id`、交期迁移、`contracts.received_amount`；末尾含 `sync_constitution` 与导航更新 |
| **`v36_drop_main_table_delivery_columns.sql`** | 删除 `contracts.delivery_date`、`purchase_orders.expected_delivery_date`；交期仅保留在明细行 |
| **`v13_role_rules_multistep_orchestrator.sql`** | `role_rules` 追加多步骤任务规则 |
| **`v14_ai_memory_scope_core_and_prompt.sql`** | `ai_memory` 允许 **`core`**；`memory_core_extract` 提示词 |

改表后请按 **「元数据流水线」** 一节执行 `CALL meta.sync_constitution()` 与 `scripts/sync_meta_pipeline.py`（若 `v12` 已跑过同步，仅新增 `v13` 时可只更新 prompts 或按需全量同步）。

---

### V7：权限体系 + 审批流 + 仪表盘 + 测试反馈（2026-04）

**Schema Shadow 压缩**

- `scripts/sync_schema_shadow.py` 输出格式从 JSON 改为紧凑纯文本（`!` 必填、`→` 外键、缩进字段行）
- 公共字段（id, status, notes, created_at, updated_at, deleted_at）声明一次不重复
- 体积从约 7000 token 压缩到约 1500 token

**权限管理**

- **角色表级权限**：`meta.role_permissions`（role_code → table_name → can_read/write/delete）；`sql_engine.py` 新增 R7 规则
- **个人权限覆盖**：`business.user_permissions`（employee_id → table_name），优先级高于角色默认；支持加权和减权
- **主管数据可见**：`employees.manager_id` + prompt 注入下属列表和 `owner_id IN (...)` 条件
- **动态菜单**：`GET /api/user-menu?user_id=`，合并个人+角色权限，前端 DataNavPanel 改用此接口驱动
- **业务表 owner_id**：`customers`、`contracts`、`purchase_orders`、`shipping_notes` 新增 `owner_id`
- 4 个角色：admin / sales / warehouse / finance，权限矩阵见 `meta.role_permissions` 种子数据

**审批流**

- `business.approvals`：审批记录（target_type、target_id、requested_by、approver_id、status、comment）
- `meta.approval_rules`：审批规则配置（target_type → approver_role）
- 合同和采购订单 INSERT 成功后自动创建审批记录，status 设为 `pending_approval`
- `GET /approvals/pending?user_id=` + `POST /approvals/{id}/decide`
- 审批通过后统一设为 `active`，下游只认 `active` 状态

**Meta Schema 屏蔽**

- `/table/{schema}` 等路由硬编码拒绝 `schema=meta`（403）
- `/nav/tables`、`/tables`、`/table-columns` 过滤 meta 表
- 前端 `/meta`、`/meta/:table` 路由重定向 `/dashboard`，NavPanel 移除元数据入口
- 无开关、无配置，设计者直接 psql

**仪表盘**

- `business.dashboard_cards`：卡片配置（title、card_type、query_sql、card_config JSONB）
- 5 种图表：number / bar / pie / line / list
- 查询优先级：个人卡片（employee_id）→ 角色默认（role_code）
- SQL 中 `:user_id` 占位符，执行时替换；所有 SQL 走 `execute_sql` R1–R7 审计
- `GET /api/dashboard?user_id=`
- 前端 ECharts + vue-echarts，5 个图表组件

**助手自我介绍**

- `role_rules` 追加自我介绍规则
- 前端空白状态展示欢迎语（不写入 ai_chat）

**测试反馈收集**

- `business.ai_incidents`：系统错误自动采集（stage / user_input / generated_sql / error_message）；`engine/incident_logger.py` 在各 except 块埋点
- `business.ai_feedback`：用户👎反馈 + 对话上下文快照；`POST /ai/feedback` + `GET /ai/feedback/summary`
- `scripts/daily_report.py`：每日汇总报告
- 这两张表 `include_in_domain_model = false`，expert 不可见

**相关迁移**

| 文件 | 作用 |
|------|------|
| `v17_permission_and_approval.sql` | 角色权限 + 审批规则 + 审批表 + owner_id + 种子数据 |
| `v18_user_permissions_and_menu.sql` | 个人权限覆盖 + manager_id 外键 |
| `v19_dashboard_cards.sql` | 仪表盘卡片 + 种子数据 |
| `v20_role_rules_self_intro.sql` | 自我介绍 prompt |
| `v21_role_rules_self_intro_wording.sql` | 自我介绍措辞修订（可选） |
| `v22_incidents_and_feedback.sql` | 错误采集 + 用户反馈 |
| `v23_business_nav_all_tables.sql` | 侧栏仅主表；业务顺序：合同→采购→到货→发货→库存→资金；admin 权限矩阵补齐 |
| `v25_nav_payments_warehouse_read.sql` | **warehouse** 角色补 `payments` 读权限 + 确保 `payments` 导航元数据 |
| `v26_nav_run_order_main_only.sql` | 侧栏仅主表；`prep` 含联系人；`run` 以合同管理为首；API 忽略 `nav_parent_table` 子表行 |
| `v27_nav_contacts_prep_contracts_first_run.sql` | 增量：联系人回「系统准备」、`contracts` 置顶「系统运行」并显示「合同管理」（旧版 v26 已跑过时执行） |

---

### V8：多步任务修复 + 中记忆层（2026-04-12）

**Orchestrator / confirm 修复**

- **`type: done` 识别修复**：tool_expert 返回 `{"type": "done", ...}` 时不再报「未知指令类型」；新增 `_expert_payload_is_done(payload)` 同时识别 `type=done` 与 `done=true` 两种写法。
- **`/ai/confirm`**：**当前行为**见下文「V9」——执行写入、更新 **`ai_tasks`**，响应 **`tool_result`**（统一形制的 **`[TOOL_RESULT]`**，并在该字符串上注入任务进度）；由前端再 **`POST /ai/chat`** 回灌，**不**在 confirm 内调用主助手。

**write_engine 异常翻译**

- `write_engine.py` 新增 `translate_db_write_exception(exc, cur)`：`ForeignKeyViolation` → `dependency_missing`（中文业务说明）；`UniqueViolation` → `duplicate`；其余 → `unknown`。`sql_engine._confirm_write_single` 在 except 中统一调用。

**tool_expert prompt 新规则（`tool_expert_executor`）**

- **禁止越权补建主数据**（v28）：发现依赖记录不存在时，立即 `type: done, success: false`，不得自行创建关联记录。
- **编号查最大值**（v29）：自动生成编号类字段（工号、单号等）前，先用 `SELECT MAX(...)` 查已有最大值 +1，只需一次查询，不循环重试。

**SQL 审计器修复**

- `sql_engine.py` 表名提取逻辑：排除 `SUBSTRING(col FROM n)` 和 `EXTRACT(field FROM col)` 函数内的 `FROM`，避免数字被误识别为表名（如 `business.4`）。

**记忆系统重构：新增中记忆层**

- 记忆层级从三层扩展为**四层**：`session → midterm（中记忆）→ longterm → core`
- `scope=midterm`：每次 `clean_session` 成功后立即调用 `merge_midterm`，将新 session 要点与已有中记忆合并（LLM 去矛盾、去重），每用户一条记录，不压缩。
- 提示词注入顺序改为 **core → longterm → midterm**，不再注入原始 session，解决 token 膨胀和旧信息污染问题。
- `compress_longterm` 原料改为 `scope=midterm`。
- `GET /ai/memory` 的 `scope` 参数新增支持 `midterm`。
- `memory_session_clean` 提示词新增规则：提取执行失败的操作及其原因，使踩坑经验能沉淀进长记忆和核心事实。

**相关迁移**

| 文件 | 作用 |
|------|------|
| `v28_tool_expert_no_auto_create_dependency.sql` | tool_expert 禁止越权补建主数据 |
| `v29_tool_expert_auto_number_max_before_write.sql` | tool_expert 编号生成前查最大值 |
| `v30_memory_midterm.sql` | `ai_memory.scope` 增加 `midterm`；插入 `memory_midterm_merge` prompt；更新 `memory_longterm_compress` 占位符 |

---

### V9：多步任务状态机（`[PLAN]` + `ai_tasks`）（2026-04-12）

将「依赖前置数据的多步操作」的规划与执行状态落在 **`business.ai_tasks`**，由 **`main.py` orchestrator** 与 **`/ai/confirm`** 更新任务行；主助手对任务进度的感知**仅**来自 **`/ai/confirm` 响应里的 `tool_result`**：在写入**真正完成**后，把 **`get_task_progress_text`** 生成的一行插在 **`[/TOOL_RESULT]`** 之前（与 **`_append_progress_line_to_tool_result`** 同形）。**Orchestrator 里专家 `done`+`success` 的 `[TOOL_RESULT]` 不拼进度**（彼时仅为预览闭环）。**tool_expert 不直接见 `ai_tasks` 表**（`include_in_domain_model = false`）。

**表与迁移**

- **`business.ai_tasks`**：会话级任务（`session_id`、`user_id`、`status`、`original_intent`、`plan` JSONB、`current_step`、`replan_count` / `max_replans`、`final_summary` 等）。迁移 **`v31_ai_tasks.sql`**（建表、索引、注释；内含 **`CALL meta.sync_constitution()`** 及将 **`ai_tasks`** 标为 **`include_in_domain_model = false`**）。
- **`role_rules` 追加 [PLAN] 规则**：多步任务须在同一次回复中先输出 **`[PLAN]`** 再输出第一步 **`[TOOL]`**；迁移 **`v32_role_rules_plan_block.sql`**（`UPDATE meta.prompts` 拼接正文）。
- **`role_rules` 步骤计数澄清**：一步 = 一次需用户在界面确认后执行的写操作；查询不占步、不计入步骤数；迁移 **`v33_role_rules_plan_steps_write_only.sql`**。

**代码**

- **`engine/task_manager.py`**：`get_active_task` / `create_task` / `update_step_result` / `advance_step` / `replan_task` / `complete_task` / `fail_task`；**`get_task_progress_text`**（会话下 **`status=running`** 任务：生成「任务进度：…」一行；若 **`plan`** 中步骤**全部**为 **`completed`** 则 **`complete_task`** 并返回「任务全部完成」）；`format_task_context`（生成 **`[TASK_STATUS]`** 文本，可供其它场景使用）；`parse_plan`。使用 **asyncpg** 连接池（与 **`psycopg2`** 的 **`db_conn()`** 并行）。
- **`main.py`**：
  - **`/ai/chat`**：主助手完整回复后若含 **`[PLAN]`**，则 **`parse_plan` → `create_task`**，并从下发与落库正文中去掉 **`[PLAN]...[/PLAN]`**（SSE 与 **`_user_visible_stream_prefix`** 一致过滤）。若含 **`[TOOL]`** → **`_call_tool_expert`**；专家闭环成功返回 **`type: "done"`** 时仅用 **`_format_tool_result`** 包成 **`[TOOL_RESULT]`**（**不**拼任务进度）。**写入预览**（`stage: preview`）同样走 **`_format_tool_result`**。保留函数 **`_format_tool_result_with_task_progress`**（当前编排器**不调用**）。
  - **`/ai/confirm`**（**`_confirm_persist_task_and_tool_row`** 等）：执行 **`confirm_batch_sql`**（及校验/专家恢复逻辑）；按成功或失败 **`update_step_result`**，成功且非最后一步则 **`advance_step`**；构造响应 **`tool_result`**（与 orchestrator 同形的 **`[TOOL_RESULT]`**，成功/失败文案见 `main.py`），并在返回前用 **`get_task_progress_text` + `_append_progress_line_to_tool_result`** 注入任务进度；**不**调用主助手。前端将 **`tool_result`** 作为 **`role=tool`** 内容再发 **`/ai/chat`**，由 orchestrator 统一落库与续写。
  - 应用生命周期内维护 **`_task_pg_pool`**（asyncpg），供 **`task_manager`** 使用。
- **依赖**：根目录 **`requirements.txt`** 含 **`asyncpg`**。

**执行迁移（示例）**

```bash
psql -U nicjiang -d aierp -f constitution/v31_ai_tasks.sql
psql -U nicjiang -d aierp -f constitution/v32_role_rules_plan_block.sql
psql -U nicjiang -d aierp -f constitution/v33_role_rules_plan_steps_write_only.sql
```

**云端同步**：库执行 v31 / v32 后部署代码并重启服务（按运维流程）。

---

## 项目目录结构概览

```
aierp/
├── main.py                 # FastAPI：表 CRUD、/auth/*、/api/user-menu、/api/dashboard、/approvals/*、/ai/chat（orchestrator+[PLAN]/[TOOL]、V9 任务池）、/ai/chat/log-message、/ai/memory/*、/ai/confirm、/ai/feedback*
├── engine/                 # sql_engine、prompt_builder、memory_manager、incident_logger、task_manager；query/write_engine 已 deprecated
├── llm/                    # OpenAI 兼容客户端（chat / 流式 / 附件解析）；intent 为占位
├── tools/                  # 连接池、通用 DB 读写与会话 memo 等辅助
├── scripts/                # sync_meta_pipeline、sync_meta_cn、sync_schema_shadow、daily_report
├── constitution/           # SQL 迁移与种子（表结构、prompts、ai_memory 等）
├── frontend/               # Vue 3 + Vite（入口见下方说明）
│   ├── index.html          # 挂载 /src/main.js
│   └── src/                # 实际使用的页面与组件（见「前端现状」）
├── dev.sh                  # 一键前后端
├── requirements.txt
└── README.md
```

`frontend/` 根目录下另有若干与 `frontend/src/` **同名或重复的 .vue / main.js**（历史拷贝），**以 `frontend/src/` 与 `index.html` 引用的 `/src/main.js` 为准**。

---

本文档用于**快速回忆**：日常命令、迁移顺序、代码与接口现状。数据库迁移与元数据同步见下文「元数据流水线」与 `scripts/migrate.py` / `scripts/seed_migrations.py`。

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
| `memory_manager.py` | 记忆管理员：`clean_session()`、`compress_longterm()`、`merge_midterm()`（中记忆合并） | **已完成**：手动 API 触发 |
| **`task_manager.py`** | **V9**：`business.ai_tasks` 读写、**`get_task_progress_text`** / **`format_task_context`**、`parse_plan`；**`get_task_progress_text`** 由 **`/ai/confirm`** 在构造 **`tool_result`** 时调用（asyncpg）；**`update_step_result`** 等亦由 confirm / 任务流调用 | **已完成** |
| `incident_logger.py` | 系统错误记录：`log_incident()` | **已完成**：在 `sql_engine` / `main.py` 各 except 块调用 |

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
| `/ai/confirm` | POST | 执行经 **`preview_sql`** 审核的写 SQL：`payload` 含 **`batch`** 或单条 **`sql`/`params`**（及预览 **`summary`** 等）；可选 **`session_id`**：**V9** 下更新 **`ai_tasks`**；返回 **`tool_result`**（**`[TOOL_RESULT]`** + 任务进度行）、**`has_active_task`** 等；**不**调用主助手；由前端将 **`tool_result`** 回灌 **`/ai/chat`** | 可用 |
| `/ai/chat/log-message` | POST | 将一条 **`assistant` / `user` / `tool` / `system`** 消息追加写入 **`ai_chat`**（如取消写入、终止流、前端异常提示） | 可用 |
| `/ai/chat` | POST | **双层 orchestrator**：`build_prompt('assistant', user_id)` + 主模型（默认 **`chat`/`chat_stream`**，可选 **`inviteExpert: true`** → **`chat_expert_only`**）；若回复含 **`[PLAN]`**（**V9**）则解析并 **`create_task`**、正文过滤 **`[PLAN]`**；若含 **`[TOOL]`** → **`_call_tool_expert`**（内层多步 **`execute_sql`/`preview_sql`**）；专家 **`done`+`success`** 时 **`[TOOL_RESULT]`** 由 **`_format_tool_result`** 生成（**不**在此处拼任务进度；进度仅在 **`/ai/confirm`** 的 **`tool_result`**）；**SSE** 默认；**`user_id`** 时建 **`ai_sessions`** 并写 **`ai_chat`** | 可用 |
| `/ai/memory/clean-session` | POST | 手动触发 session 清洗，要点写入 `business.ai_memory`（`scope=session`） | 可用（body：`{"session_id": <int>}`） |
| `/ai/memory/compress-longterm` | POST | 手动触发长期记忆压缩，覆盖/写入 `scope=longterm`；成功后**顺带**调用 `extract_core_facts` 更新 `scope=core` | 可用（body：`{"user_id": <int>}`） |
| `/ai/memory/extract-core` | POST | 仅提炼核心经验（写入 `scope=core`） | 可用（body：`{"user_id": <int>}`） |
| `/ai/memory` | GET | `user_id` + `scope`（`core` \| `longterm` \| `midterm`）列记忆行 | 可用 |
| `/ai/memory` | POST | 新增一条 `core` 或 `longterm`：`{ user_id, scope, content }` | 可用 |
| `/ai/memory/{id}` | PATCH | 更新内容：`{ user_id, content }`（仅 `core`/`longterm`） | 可用 |
| `/ai/memory/{id}` | DELETE | `?user_id=` 与记录一致时删除 | 可用 |
| `/api/user-menu` | GET | 当前用户可见菜单（个人+角色权限合并） | 可用 |
| `/api/dashboard` | GET | 仪表盘数据（执行卡片 SQL 返回结果） | 可用 |
| `/approvals/pending` | GET | 当前用户待审批列表 | 可用 |
| `/approvals/{id}/decide` | POST | 审批决定（approved/rejected） | 可用 |
| `/ai/feedback` | POST | 提交用户反馈 | 可用 |
| `/ai/feedback/summary` | GET | 反馈摘要（开发者用） | 可用 |
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
| `router/index.js` | `/dashboard`、`/business`、`/business/:table`、`/memory`、`/ai-rules`；**`/meta`、`/meta/:table` 重定向 `/dashboard`** | 可用（登录页不走路由，由 `App.vue` 条件渲染） |
| `stores/app.js` | API 基址来自 `VITE_API_BASE`（`frontend/.env.development` 默认 `http://localhost:8000`，`.env.production` 为空走同域）；请求 `/tables`、`/nav/tables`、`/table-columns`、`/table/...`、导出 URL、表 CRUD；登录后 **`fetchUserMenu`**（`/api/user-menu`） | 可用 |
| `components/NavPanel.vue` | 侧栏导航；**展示当前用户名 + 退出**；快捷链接 **仪表盘 / 记忆**（无元数据入口、无侧栏「AI 规则」） | 可用 |
| `components/DataNavPanel.vue` | 数据侧导航：**`GET /api/user-menu`** 驱动可见表（个人+角色权限合并） | 可用 |
| `components/AiPanel.vue` | AI 对话：SSE **`/ai/chat`**、**`/ai/confirm`**；加载中可 **「终止」** 中断 SSE；**空白欢迎语（不入库）** + **👎 反馈** → **`POST /ai/feedback`**；请求体 **`user_id` 取自 `useAuthStore().currentUser?.id`** | 可用 |
| `views/DashboardView.vue` | **动态仪表盘**（ECharts / vue-echarts，卡片数据来自 **`/api/dashboard`**） | 可用 |
| `components/dashboard/*.vue` | 图表组件：**NumberCard** / **BarCard** / **PieCard** / **LineCard** / **ListCard** | 可用 |
| `views/MetaView.vue` / `BusinessView.vue` | 元数据/业务表列表入口（**`/meta` 已重定向仪表盘**，一般不从路由进入） | 可用 |
| `views/TableView.vue` | 通用表浏览与编辑 | 可用 |
| `views/AiRulesView.vue` | `/ai-rules` 占位说明：**`meta.ai_rules` 请用 psql 维护**，无表数据请求 | 可用 |
| `views/MemoryView.vue` | **记忆管理**：`core` / `longterm` 查看与增删改（`/ai/memory`） | 可用 |
| `config/tableSplit.js` | 表拆分配置 | 可用 |
| `utils/tableExport.js` | 导出 xlsx URL 构造 | 可用 |

---

## 已联通 vs 未联通（总结）

- **表数据与导航**：前端 `stores/app.js` ↔ 后端表 API（`/tables`、`/nav/tables`、`/table/...`、导出）**已串通**。
- **登录**：`LoginView` → **`/auth/login`**；刷新 **`/auth/me`**；**`AiPanel` 携带同一 `user_id`** → **已实现（待测试）**。
- **对话主链路**：用户输入 → `AiPanel` → `POST /ai/chat`（SSE）→ **外层**主模型 + **`[TOOL]`** → **`_call_tool_expert`**（内层）→ **`sql_engine`**；**`[TOOL_RESULT]`** 回注外层，外层最多 **`MAX_ORCHESTRATOR_ROUNDS`（与 `MAX_TOOL_ROUNDS` 同义，当前 30）** 轮；详见上文 **「assistant → orchestrator → tool_expert」**。流式进行中可点 **「终止」** 断开 SSE；服务端可写入系统提示「用户已终止自动执行。」
- **查询 / 写入**：意图块 **`[TOOL]`** → 内层 expert 产出 **SQL** → `execute_sql` / `preview_sql` → 前端确认 → **`/ai/confirm`**（`payload` + 可选 **`session_id`**）→ **`confirm_batch_sql`**（旧版结构化 query/write 主路径已弃用）。
- **记忆系统**：`clean-session` / `compress-longterm` 写入 `business.ai_memory`；主助手注入长期 + 今日 session 要点；**新开聊时若上一会话未整理则自动 `clean_session` 一次**（见上）；凌晨定时压缩仍待实现。
- **会话持久化**：**已联通**——提供有效 **`user_id`**（登录态）时自动建 **`ai_sessions`** 并写 **`ai_chat`**（须满足 `employees` 外键）。

---

## 环境与启动

- **Python**：项目根目录使用虚拟环境 `.venv`（`source .venv/bin/activate`）。
- **依赖（V5 相关）**：`bcrypt`（登录）、`sqlparse`（`sql_engine` 审计）、**`asyncpg`**（**V9** `task_manager` / **`main.py`** 任务池），见根目录 **`requirements.txt`**。
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
3. `scripts/sync_schema_shadow.py` — 按 `include_in_domain_model` 裁剪后生成**紧凑纯文本**（非 JSON），写入 `meta.prompts`（`prompt_code = schema_shadow`）

一键（根目录）：

```bash
.venv/bin/python scripts/sync_meta_pipeline.py
```

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
| **`v6_role_rules_erp_operator_data_only.sql`** | **V6**：`role_rules` + `tool_query_interface`——主助手为 **ERP 操作员**，业务事实须先 `[TOOL]` 查库，禁止编造 |
| **`v7_tool_expert_plan_then_execute.sql`** | **V7**：`tool_expert_executor`——**先规划步骤再执行**；合同/多表写入对齐 schema；与引擎无参 `LIKE '%x%'` 修复配合 |
| **`v8_tool_expert_contract_write_one_round.sql`** | **V8**：`tool_expert_executor`——**首个 `write` 预览成功即结束专家轮次**；合同+行项目须 **一条 SQL（CTE）** 或确认主表后再聊子表 |
| **`v9_tool_expert_multi_write_then_done.sql`** | **V9**：在 `tool_expert_executor` 末尾追加说明——**多条 write 暂存后 `done` 汇总**；与 orchestrator 多写入一致 |
| `update_role_rules_memory_prompt.sql` | 更新 `role_rules` 全文（记忆与数据规则） |
| `alter_ai_sessions_status_default.sql` | `ai_sessions.status` 默认 `in_progress` |
| `fix_ai_chat_primary_key_if_wrong.sql` | 若误将 `ai_chat` 主键建在 `session_id` 上则修复为 `id`（正常库为 no-op） |
| `drop_business_feedback.sql` / `drop_business_sessions_frames_slots_turns.sql` / `nav_workflow_contract_items.sql` | 按需：删旧表或导航种子（见各文件头注释） |
| **`drop_workflows_tables.sql`** | 删除 **`workflows` / `workflow_steps` / `workflow_steps_legacy`**；末尾 **`CALL meta.sync_constitution()`** |
| **`v10_purchasing_suppliers_po.sql`** | **V6**：供应商、采购订单、采购明细（见上节「V6」） |
| **`nav_purchasing_tables.sql`** | **V6**：侧栏数据导航：供应商与采购主从 |
| **`v11_receiving_shipping_tables.sql`** | **V6**：到货/发货四表 + `contract_items.shipped_qty` |
| **`v12_payments_inventory_delivery_fix.sql`** | **V6**：收付款、库存、交期子表、发货关联库存等（见上节） |
| **`v13_role_rules_multistep_orchestrator.sql`** | **V6**：`role_rules` 多步骤任务与征得同意 |
| **`v14_ai_memory_scope_core_and_prompt.sql`** | **V6**：`ai_memory.scope` 增加 **`core`**；`meta.prompts` 插入 **`memory_core_extract`** |
| **`v15_comment_new_tables.sql`** | **V6**：新建十张业务表及若干新增列的 **COMMENT ON TABLE/COLUMN**（中文）；末尾 **`CALL meta.sync_constitution()`** |
| **`v17_permission_and_approval.sql`** | **V7**：角色权限 + 审批规则 + 审批表 + `owner_id` + 种子数据 |
| **`v18_user_permissions_and_menu.sql`** | **V7**：个人权限覆盖 + `manager_id` 外键 |
| **`v19_dashboard_cards.sql`** | **V7**：仪表盘卡片表 + 种子数据 |
| **`v20_role_rules_self_intro.sql`** | **V7**：`role_rules` 追加自我介绍 |
| **`v21_role_rules_self_intro_wording.sql`** | **V7**：自我介绍措辞修订（可选） |
| **`v22_incidents_and_feedback.sql`** | **V7**：`ai_incidents` + `ai_feedback`，`include_in_domain_model=false` |
| **`v23_business_nav_all_tables.sql`** | **V7+**：侧栏**不含子表明细**；`run` 顺序为销售合同→采购→到货→发货→库存→资金；**admin** `role_permissions` 全表补齐 |
| **`v25_nav_payments_warehouse_read.sql`** | **V7+**：`warehouse` 补 **`business.payments`** 读权限（v17 遗漏）；保证侧栏出现收付款 |
| **`v26_nav_run_order_main_only.sql`** | **V7+**：侧栏**仅主表**；`prep` 含**联系人**；`run` 以**合同管理**（`contracts`）为首→变更→采购→收付款→到货→发货→库存；**`/api/user-menu`** 排除子表导航 |
| **`v27_nav_contacts_prep_contracts_first_run.sql`** | **V7+**：曾执行**旧** v26 的库：联系人回 prep、`contracts` 置顶 run |
| **`v28_tool_expert_no_auto_create_dependency.sql`** | **V8**：tool_expert 禁止越权补建主数据 |
| **`v29_tool_expert_auto_number_max_before_write.sql`** | **V8**：tool_expert 编号生成前查最大值（MAX+1，单次查询） |
| **`v30_memory_midterm.sql`** | **V8**：中记忆层（`scope=midterm`）+ `memory_midterm_merge` prompt |
| **`v31_ai_tasks.sql`** | **V9**：**`business.ai_tasks`** 任务状态机表；**`include_in_domain_model = false`** |
| **`v32_role_rules_plan_block.sql`** | **V9**：**`role_rules`** 追加多步任务 **`[PLAN]`** 格式与触发规则 |
| **`v33_role_rules_plan_steps_write_only.sql`** | **V9**：**`role_rules`** 澄清 **[PLAN]** 步骤计数（一步 = 一次需确认的写；查询不占步） |

执行示例：

```bash
psql -U nicjiang -d aierp -f constitution/create_meta_prompts.sql
```

**改表或种子后**：再跑一次 `CALL meta.sync_constitution();`（若需把新表同步进 `meta_*` 目录）。

---

## 容易混的表（心智模型）

- **`meta.prompts`**：所有注入 LLM 的提示词模块；字段 **`agent_role`** 区分归属。`inject_mode`：`always` = 拼装进 `build_prompt(agent_role)`，`on_demand` = 仅 `build_prompt_on_demand(prompt_code)`（如记忆清洗/压缩脚本）。
- **`business.ai_memory`**：用户级 AI 记忆（`scope`：`session` / `midterm` / `longterm` / `core`），`source_session_id` 可选指向 **`business.ai_sessions`**。
- **`business.ai_sessions` / `business.ai_chat`**：对话会话与消息原文；持久化由 `/ai/chat` 在提供 `user_id` 时写入。
- **`business.ai_tasks`**：**V9** 多步任务状态（`plan` JSONB、当前步、重规划次数等）；**`include_in_domain_model = false`**，由代码与 **`task_manager`** 维护，**不**注入 tool_expert。任务进度文案给主助手的入口在 **`/ai/confirm` 响应 `tool_result`**（写入完成后注入）；**`/ai/chat`** 里专家 **`done`+success** 的 **`[TOOL_RESULT]`** **不含**进度行。
- **`meta.role_permissions`**：角色→表级权限矩阵，**R7** 校验用。
- **`business.user_permissions`**：个人级权限覆盖，优先于角色默认。
- **`meta.approval_rules`**：审批规则配置（`target_type` → `approver_role`）。
- **`business.approvals`**：审批记录。
- **`business.dashboard_cards`**：仪表盘卡片配置，存 SQL，前端经 **`/api/dashboard`** 由引擎执行。
- **`business.ai_incidents`**：系统错误自动记录（`include_in_domain_model=false`，expert 不可见）。
- **`business.ai_feedback`**：用户反馈（`include_in_domain_model=false`，expert 不可见）。

**`agent_role` 与典型 prompt 条目（V5 之后）**

| `prompt_code` | `agent_role` | 说明 |
|---------------|----------------|------|
| `role_rules` | assistant | 人格与行为规则 |
| `tool_query_interface` | assistant | **V5 重写**：意图级菜单，约定 **`[TOOL]`**（自然语言意图，**不含**表名/字段名） |
| `tool_write_interface` | assistant | **V5 重写**：同上 |
| `schema_shadow` | tool_expert | 领域表结构紧凑文本（`include_in_domain_model` 裁剪；公共字段仅文首声明） |
| **`tool_expert_executor`** | **tool_expert** | **V5 新增**：教 expert 输出 **`type: query|write`** 的 JSON + 多步闭环 + **`done`**；与 **`sql_engine`** 执行链路一致 |
| `tool_query` | tool_expert | **V5 停用**（`is_active=false`）：旧版结构化 query 说明，由 SQL 路径取代 |
| `tool_write` | tool_expert | **V5 停用**（`is_active=false`）：旧版结构化 write 说明，由 SQL 路径取代 |
| `memory_session_clean` | memory_manager | 会话清洗（on_demand） |
| `memory_longterm_compress` | memory_manager | 长期压缩（on_demand） |
| `memory_midterm_merge` | memory_manager | 中记忆合并（on_demand）：将新 session 要点合并进中记忆，去矛盾去重 |

> **`build_prompt('tool_expert')`** 只加载 **`is_active = TRUE`** 且 **`inject_mode = 'always'`** 的片段，因此停用的 **`tool_query` / `tool_write`** 不会进入 expert 的 system prompt；典型注入组合为 **`schema_shadow` + `tool_expert_executor`**。

---

## 其他脚本（按需）

- `scripts/sync_schema_shadow.py`：单独生成 schema 影子（紧凑文本）到 `meta.prompts`
- `scripts/sync_meta_cn.py`：单独补字段中文名
- `scripts/daily_report.py`：每日测试摘要（`ai_incidents` + `ai_feedback`），用法 `python scripts/daily_report.py [--days N]`
