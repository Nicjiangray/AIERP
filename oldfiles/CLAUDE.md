# CLAUDE.md — AI·ERP 项目当前状态说明

**最高核心架构目标**
每次读到都要问一下，对齐了没有。
**设计哲学：三原子通用引擎**

整个系统只有三个原子工具驱动所有业务：

- **query**：读数据库（只读 SELECT）
- **collect**：从对话和查询结果中提取业务要点，更新 memo
- **write**：写数据库（含业务表写入和 memo 更新），受校验和约束保护

LLM 拿到所有 workflows 预制的业务知识（表结构、裁剪后的字段、关联关系、约束规则），自主判断每一轮该调用哪个原子。代码层不做意图识别和分流。

**一个入口，一个引擎**

用户消息进入后，不经过 `detect_intent` 前置分流，不区分 free_chat / collecting / query 等模式。统一引擎函数接收用户输入，system prompt 注入全部 workflows 的业务知识摘要，LLM 自主决策执行路径。

**memo 机制**

memo 是 session 级别的结构化记忆（entities / facts / hints），存储在 `sessions.memo` 字段。其本质是信息的过滤和审核——LLM 从对话和查询结果中提炼业务要点，用户可见、可纠正。memo 的更新本身就是一次 write 原子操作。当用户从查询/讨论转向写操作时，memo 作为已知上下文直接注入，跳过重复信息收集，提高命中率和准确性。

**安全边界**

- 写操作受引擎层校验和约束保护（白名单、必填校验、人工确认）
- 注入给 LLM 的字段经过裁剪（ask_if_missing 控制 prompt 注入，is_required 控制提交校验）
- memo 是显性的，用户看得到、能纠正，LLM 的判断错误有修正通道

**workflows 的角色**

workflows 和 workflow_steps 不是"流程控制器"，而是"业务知识库"。它们告诉 LLM：系统有哪些业务能力、每种业务涉及哪些表和字段、字段的类型和约束是什么。LLM 根据这些知识自主完成 query → collect → write 的循环。

**设计原则沿用**

计算归数据库，编排归引擎，决策归 LLM，安全归代码。


> **非技术 / 产品**：见 **`docs/PM_项目地图.md`**（业务主流程与协作说明）；研发速览见 **`docs/技术主路径与整理清单.md`**。
>
> 本文件供 Cursor 理解项目结构，写新代码前请先读这里。
> 有效文件、作废文件、架构逻辑均以本文件为准。
>
> **近期架构**：工具声明已入库（`meta.meta_tools` + `constitution/create_meta_tools.sql`）；collecting 通过 `load_tools_from_db()` 动态注入 LLM 工具列表（`engine/agent_tools.TOOLS` 仅作 fallback）。推理侧已收敛为**单一本地主模型**：**`.env` 中 `LLM_MODEL_NAME=gemma4:26b`**（可按机器改为其它已拉取的 Ollama 模型）。**collecting 内无模型分流**：`run_collecting` → `chat_with_tools` 等处 **`prefer_fallback=False` 固定**，不按意图或步骤切换云端；`.env` 中 **FALLBACK_*** 仅保留给**非 collecting** 路径或其它调用点显式 `prefer_fallback=True` 时使用，**collecting 不依赖 DeepSeek**。
>
> **主数据查询**：`query_master_data` 已与增删改一并走 **`run_collecting`**（`engine/workflow_runner.execute_workflow_for_chat` 分发），不再经 `run_workflow` / `workflow_steps` 预置 SQL；LLM 用 **`query_db`** → `execute_query_db`（含业务表 **`deleted_at IS NULL` 自动过滤**，除非 SQL 已出现 `deleted_at`），成功结果写入 **`ctx["data"]["main"]`** 供前端表格。`business.workflows` 中仍保留 `query_master_data` 行供意图识别；`run_query_master_data()` 仍存在于 `workflow_runner`（如 `tools` 注册表），**HTTP 主路径不再调用**。
>
> **合同创建（跨表）**：意图 **`create_contract`** 走 **`run_collecting`**；**`submit_contract`** → `execute_submit_contract` → 预览 **`pending_contract`**；若 LLM 走统一 **`submit`**，则由 **`pending_submit_v15b`** 按 **`workflow_steps`** 组装 **`pending_submit.writes`**。**`/ai/confirm`** 侧由 **`run_confirm_generic_writes`** 执行待写入。**`contract_no` 由触发器 `trg_contracts_no` 生成**；预览中可展示「确认后由系统自动生成」。
>
> **V15 submit / 确认主路径（架构）**：collecting 侧统一工具 **`submit`**（`execute_submit` → **`engine/pending_submit_v15b.execute_submit_with_arguments`**）：按 **`business.workflow_steps`** 的 write 步骤（**`data_key` / `where_field` / `depends_on`**）组装 **`context_snapshot.pending_submit.writes`**；**`/ai/confirm`** 仅调 **`engine/workflow_engine.run_confirm_execution`** → **`engine/workflow_engine_exec.run_confirm_generic_writes`**，在同一事务内执行 **`do_insert` / `do_update` / `do_soft_delete`**（支持单行与 **list 多行**）。**`engine/data_sanitize.sanitize_step_data`** 在 submit 组装后与 confirm 执行前各做一次清洗。意图 **`update_contract`** 的主路径为 **`submit`**：`data` 含 **`record_id`、`reason`、`header_updates` / `items_add` / `items_update` / `items_remove`**（按需任选），与库中 **`constitution/v15d_update_contract_steps.sql`** 定义的 write 步一致；**不再**经专用的 `commit_update_contract`。工具名 **`update_contract`**（`execute_update_contract`）仍保留作预览/兼容，返回 **`pending_contract_update`**，但 HTTP 主路径以 **`submit`** 为准。
>
> **合同删除 + 变更证据链**：意图 **`delete_contract`** 用 **`delete_entity`**（`table_name=contracts`）+ **`submit`** 多 write；**`contract_changes`** 的 `snapshot_before` 在 submit 阶段查询，**`snapshot_after`** 在确认事务内前序写完成后由引擎补全（删除场景可为 `{"deleted": true}` 占位，不覆盖）。
>
> **V16 修复与改进**：
> - **字段裁剪**：`meta.meta_fields.ask_if_missing` 校准为「该字段是否需要 LLM 从用户获取」；`meta.sync_workflow_step_fields()` 对 collect/write 步骤只注入 `ask_if_missing=true` 的字段，消除 LLM prompt 中的系统字段污染。审核视图 `meta.v_field_responsibility`。
> - **工具名统一**：所有意图的 `prompt_rules`、`allowed_tools`、`workflow_steps.instruction` 统一为 **query_db / submit / ask_user** 三工具；旧工具名（write_entity / update_entity / delete_entity / submit_contract / update_contract）仅在 `tool_executor.py` 保留执行函数，不再出现在 LLM 可见的文案中。
> - **submit 归一化**：`pending_submit_v15b` 新增 `_normalize_simple_entity_work`（simple_entity 扁平 data → table_name + fields + record_id）和 `_normalize_and_validate_contract_work`（合同 contract+items 结构归一化）；submit 必填校验改为只检查 `meta_fields.is_required`，不再用 `ask_if_missing`。
> - **合同写入白名单**：`contracts` / `contract_items` 纳入 `pending_submit_v15b` 与 `workflow_engine_exec` / `collecting.build_col_vals_from_fields` 的写入路径；`_resolve_depends_dict` 修复 **contract_id** 传递（勿用「首个空 *_id」误填 `product_id`）。
> - **session 隔离**：`/ai/chat` 支持客户端传入 `session_id`（`_sync_validate_and_touch_session` 校验归属与活跃状态）；`scripts/test_e2e_chat.py` 每场景独立 session。

---

## 一、作废文件（不要参考，不要引用，不要修改）

| 文件 | 原因 |
|------|------|
| `db_pool.py` | 已迁移至 `tools/database.py` |
| `llm_client.py` | 已迁移至 `llm/client.py` |
| `llm_tools.py` | 已迁移至 `llm/tools.py` |
| `llm/intent.py` | 占位空实现，意图识别逻辑在 `engine/intent.py` |
| `frontend/main.py` | 独立测试用 FastAPI，非生产入口 |
| `frontend/*.vue`（根目录散落） | Vite 入口不加载，以 `frontend/src/` 下文件为准 |
| `frontend/main.js` / `app.js` / `index.js`（根目录） | 同上 |

---

## 二、有效文件（以这些为准）

### 后端

```
main.py                     FastAPI 主应用（`/ai/chat`：**detect_intent** + collecting **supersede**；可选请求体 **`session_id`** 指定会话；`/ai/confirm`；**`POST /ai/feedback`** 用户反馈入库），唯一 HTTP 入口
tools/database.py           数据库连接池（双池）、元数据读取、工作流 SQL 执行
tools/__init__.py           TOOLS 注册表
tools/file_reader.py        占位：read_pdf / read_excel / read_image
tools/file_writer.py        占位：export_excel / generate_pdf
engine/workflow.py          工作流执行引擎，run_workflow()
engine/workflow_runner.py   execute_workflow_for_chat()：collecting 类 intent（含 **create_contract** / **update_contract** / **delete_contract**）调 run_collecting；其余 intent 调 run_workflow；run_query_master_data() 仍保留（非 /ai/chat 主路径）
engine/workflow_engine.py   **run_confirm_execution** → 仅调 **run_confirm_generic_writes**（V15-D）；**commit_insert_from_slots** / **commit_update**（测试或旧路径）
engine/workflow_engine_exec.py  **run_confirm_generic_writes**：遍历 **pending_submit.writes**，**do_insert** / **do_update** / **do_soft_delete**（含多行 list）；写入白名单含 **contracts / contract_items / contract_changes**；**contract_changes** 的 **snapshot_after** 在事务内补全；**_resolve_depends_dict** 显式补 **contract_id**
engine/workflow_steps_util.py   **load_workflow_steps_for_intent**、**infer_write_operation** / **infer_operation_from_intent**、**extract_data_for_step**
engine/pending_submit_v15b.py   **execute_submit_v15b** / **execute_submit_with_arguments**：按 **workflow_steps** 组装 **pending_submit**；**_normalize_simple_entity_work**（扁平 data 归一化）；**_normalize_and_validate_contract_work**（合同 contract+items）；合同三意图对 contracts/contract_items/contract_changes **跳过 simple 表白名单**（见 **_use_simple_table_whitelist**）
engine/data_sanitize.py         **sanitize_step_data**：submit 与 confirm 前通用字段清洗；必填校验只用 **meta_fields.is_required**（不用 ask_if_missing 作必填）
engine/collecting.py        collecting：run_collecting()（LLM tool 循环）、query、工具预览、**apply_pending_submit_to_frame**（**pending_submit** 写入 frame，供确认）；合同预览 **pending_contract** / **pending_contract_update**（兼容 **update_contract** 工具）
engine/agent_tools.py       工具定义：硬编码 TOOLS（fallback）+ load_tools_from_db()（主路径，从 meta.meta_tools 动态加载）
engine/tool_executor.py     工具执行与校验：execute_query_db、execute_write_entity、execute_update_entity、execute_delete_entity、**execute_submit**（→ pending_submit_v15b）、**execute_submit_contract**、**execute_update_contract**、execute_tool_call
engine/turns.py             对话轮次读写，append_turn() / get_recent_turns()，get_recent_turns_sync 供同步 collecting 使用
engine/intent.py            意图识别，detect_intent()，extract_query_params()，explain_capabilities()
llm/client.py               LLM 连接，chat / chat_stream / chat_with_fallback；chat_with_tools（tools + prefer_fallback，collecting 当前固定 False）
llm/tools.py                DB 工具链，chat_with_db_outcome_stream 等
scripts/test_executor_guards.py   回归：tool_executor 校验（无 LLM，需 DB）
scripts/test_e2e_chat.py          端到端：HTTP `/ai/chat` + `/ai/confirm`（含 **合同创建** 等场景；需后端与 LLM，结果受模型随机性影响）
```

### 前端（以 frontend/src/ 为准）

```
frontend/src/main.js
frontend/src/App.vue
frontend/src/router/index.js
frontend/src/stores/app.js
frontend/src/components/NavPanel.vue
frontend/src/components/DataNavPanel.vue
frontend/src/components/AiPanel.vue   （每条 AI 回复可点 **反馈** → `POST /ai/feedback`）
frontend/src/views/DashboardView.vue
frontend/src/views/MetaView.vue
frontend/src/views/BusinessView.vue
frontend/src/views/TableView.vue
frontend/src/views/AiRulesView.vue
frontend/src/utils/tableExport.js
```

### 新增 / 种子（意图与 collecting）

```
constitution/add_workflow_intent_embeddings.sql     example_queries / query_embeddings 字段
constitution/seed_create_simple_entity_workflows.sql  create_simple_entity / update_simple_entity 示例句（ON CONFLICT 更新）
constitution/create_meta_tools.sql                  meta.meta_tools 建表 + 工具种子（ON CONFLICT 更新）
constitution/add_deleted_at.sql                     业务表 / 元数据表 deleted_at + delete_entity 工具 + delete_simple_entity 意图（按需）
constitution/add_create_contract.sql                **submit_contract** 工具 + **create_contract** 意图（示例句；执行后需 `sync_intent_embeddings`）
constitution/add_contract_changes.sql               **contract_changes** 变更证据链表
constitution/add_update_contract.sql                **update_contract** 工具 + 意图
constitution/add_delete_contract.sql                **delete_contract** 意图（执行仍 **delete_entity**）
constitution/rename_products_name_to_product_name.sql  **products** 列 `name` → **`product_name`**（与 LLM / 合同明细命名一致；执行后建议 `CALL meta.sync_constitution();` 再 `sync_meta_cn.py`）
constitution/add_feedback.sql                         **business.feedback** 用户反馈表 + `meta.meta_tables` 登记
constitution/v15b_workflow_steps.sql                **workflow_steps.data_key**；create_contract / delete_contract 多 write 步
constitution/v15c_workflow_steps.sql                **workflow_steps.where_field**（如 contract_items 软删按 **contract_id**）
constitution/v15d_update_contract_steps.sql       **update_contract** 拆为多 write 步（**data_key**：header_updates / items_add / items_update / items_remove + contract_changes）
scripts/sync_intent_embeddings.py                   向量同步，改 workflows 后执行
scripts/test_intent_layers.py                       三层意图识别测试
constitution/v16a_fix_ask_if_missing.sql            5 张业务表 ask_if_missing 校准
constitution/v16a_field_responsibility_view.sql      meta.v_field_responsibility 审核视图
constitution/v16b_workflow_steps_tool_names.sql      workflow_steps instruction/tool 列旧工具名清理
constitution/v16c_meta_tools_submit_hint.sql       meta_tools 中 submit 工具描述更新（支持扁平格式说明）
constitution/patch_create_contract_optional_prompt.sql  create_contract 可选字段不追问规则（幂等追加 prompt_rules）
```

---

## 三、当前架构

### 工具声明三层分离

```
声明层（数据库 meta.meta_tools）
  ├── 工具名、描述、参数 schema  → 注入 LLM 的 function calling 工具列表
  ├── 校验规则（validations）    → 未来供通用校验引擎使用（当前仅存储，执行仍在代码）
  └── 元信息（tool_type / confirm_required / enabled / sort_order）

执行层（代码 engine/tool_executor.py）
  ├── execute_tool_call()        → 按工具名分发到具体执行函数
  ├── execute_query_db()         → SQL 防护 + 只读查询 + 可选自动 `deleted_at IS NULL` 包装
  ├── execute_write_entity()     → 字段校验 + pending_confirm
  ├── execute_update_entity()    → verified_record_id + pending_confirm
  ├── execute_delete_entity()    → verified_record_id + pending_confirm（软删预览）
  ├── execute_submit()           → 调 **pending_submit_v15b**；simple_entity 走 **_normalize_simple_entity_work**（从 data/frame 提取 table_name + record_id + fields）；合同走 **_normalize_and_validate_contract_work**（归一化 contract+items）；按 **workflow_steps** 组装 **pending_submit.writes**（不写库）
  ├── execute_submit_contract()  → 主表+明细校验 + pending_confirm（**pending_contract**；兼容路径）
  └── execute_update_contract()  → 合同变更校验 + pending_confirm（**pending_contract_update**；工具兼容，主路径优先 **submit**）

安全层（代码，不可数据库化）
  ├── SQL 注入防护（_validate_agent_select_sql）
  ├── verified_record_id 强制覆盖
  ├── 表白名单 / 字段白名单（读 meta_tables / meta_fields）
  └── 确认流程（pending_confirm → /ai/confirm）
```

**设计原则**：声明归数据库（新增工具只需插数据），执行和安全归代码（不可由数据驱动）。
**扩展方式**：新增工具时，往 meta.meta_tools 插一条记录（LLM 自动看到），在 tool_executor.py 加一个 elif 分支 + 执行函数。

### 主流程

```
用户自然语言输入
    ↓
engine/intent.py：detect_intent()
    LLM 读 business.workflows 识别意图 → 返回 intent key
    未命中 → explain_capabilities() 引导用户
    ↓
命中 **collecting 类 intent**（create_simple_entity / update_simple_entity / delete_simple_entity / **query_master_data** / **create_contract** / **update_contract** / **delete_contract**）
    → engine/collecting.py：run_collecting()
        get_or_create_frame（frames / slots / context_snapshot；**query 场景允许 target_table 为空**；**create_contract / update_contract / delete_contract** 均固定 **target_table=contracts**；**create_contract** 不初始化 slots，由 **submit_contract** 或 **submit** 收集；**update_contract** 主路径为 **`submit`**（`data` 含 **record_id / reason / header_updates / items_\***）；仍可选用 **update_contract** 工具 → **`pending_contract_update`**（兼容）；**delete_contract** 用 **delete_entity** + **submit** 多 write 预览软删）
        → 从 meta.meta_tools 动态加载工具列表（load_tools_from_db()，失败 fallback 到硬编码 TOOLS）
        → LLM tool 循环（chat_with_tools + 动态工具列表，`prefer_fallback=False`；默认最多 **6** 次工具调用，**`create_contract` / `update_contract`** 为 **10** 次）
        → **意图识别 detect_intent()** 与其它步骤同样走本地主模型配置（`LLM_*`）。
        → **文案**：CUD 用 `_COLLECTING_STRICT_TOOL_RULES`；**query_master_data** 用 `_COLLECTING_QUERY_RULES`（只读，不调用 write/update/delete）；**create_contract** 用 **`_COLLECTING_CONTRACT_RULES`**（**submit_contract** / **submit** + query_db + ask_user）；**update_contract** 用 **`_COLLECTING_CONTRACT_UPDATE_RULES`**（**submit** 为主，**reason** 必填；**update_contract** 工具可选）；**delete_contract** 用 **`_COLLECTING_CONTRACT_DELETE_RULES`**（**delete_entity** + **submit** + query_db + ask_user）。
        → **新建代码预填**（仅 create）：若本轮 LLM **未调用任何工具**且 intent 为 create，则确定性填槽；
          必填齐时 **代码直接 `execute_tool_call("submit")`**（内部可走 **write_entity** 等价校验，见日志 `collecting: 必填字段齐全，代码强制调用 submit`）。
        → query_db → execute_query_db；**query_master_data** 时将成功返回的 **rows 写入 ctx["data"]["main"]**（供前端表格）
        → write_entity / update_entity / delete_entity / **submit** / submit_contract → tool_executor 校验后 pending_confirm，不直接写库
        → ask_user → 追问
        新建：apply_preview_to_slots（source = agent_preview）供确认后 INSERT（旧路径）
        更新：apply_update_preview_to_frame（pending_update）
        删除：apply_delete_preview_to_frame（pending_delete）
        合同新建：apply_contract_preview_to_frame（**pending_contract** + action=create_contract；**`UPDATE business.frames` 合并 `context_snapshot`**）
        合同修改（工具兼容）：apply_contract_update_preview_to_frame（**pending_contract_update** + action=update_contract）
        **V15 主路径**：工具返回 **pending_submit** 时 **apply_pending_submit_to_frame** 写入 **context_snapshot**（确认前须落库，供 **POST /ai/confirm** 读取）
    ↓
main.py `/ai/chat` **意图路由**（普通消息，非 confirm / pending_token）：**每条用户话都调用 `detect_intent`**（不再因存在 collecting frame 而跳过）。若存在 **status=collecting** 的旧 frame，且 **本轮识别意图与 `frame.workflow_intent` 不一致** → **`_sync_supersede_collecting_frame`**，将旧 frame 的 **`status` 置为 `cancelled`**；若 **`infer_target_table_from_user_text`** 与 frame 内 **target_table** 仍不一致 → 同样 supersede。**`matched_intent`**：无 frame 时用识别结果；有 frame 且识别为 `None` 时沿用 frame 意图。
    ↓
main.py /ai/chat 若 ctx.status == pending_confirm → 响应带 confirm_required、preview、explanation、frame_id；工作流 JSON 另带 **session_id**、**frame_id**（供前端反馈等）
    ↓
main.py POST /ai/confirm { frame_id, confirmed, user_id? }
    confirmed=true  → **workflow_engine.run_confirm_execution** → **workflow_engine_exec.run_confirm_generic_writes**（读 **context_snapshot.pending_submit.writes**，单事务执行；**merge_execution_result_into_frame** 写回结果）
                      HTTP JSON 带 **confirm_result**：**success** / **error**（失败时 **error_detail**）
    confirmed=false → **confirm_result=cancelled**；frame 置 collecting；DELETE slots WHERE source=agent_preview；jsonb 去掉 pending_update / **pending_delete** / **pending_contract** / **pending_contract_update** / **pending_submit** / action
**其它 intent**（非上述 collecting 清单）→ engine/workflow.py：run_workflow()（全步骤 on_fail=skip）
    → engine/workflow_runner.py：LLM 总结或步骤内 reply
    ↓
execute_workflow_for_chat() 返回统一结构给 main.py /ai/chat
    {reply, data:{main, sub}, content, workflow_context?, confirm_required?, session_id?, frame_id?}
```

**架构红线**：LLM 永远不直接执行数据库操作，必须经过代码层（含 tool 调用由 `tool_executor` 执行）。

**链路可观测性（日志前缀，便于 grep）**：HTTP 与核心路径使用统一标签，例如 **`[chat.入口]` / `[chat.出口]` / `[chat.supersede]`**；**`[confirm.入口]` / `[confirm.snapshot]` / `[confirm.分支]` / `[confirm.结果]` / `[confirm.取消]`**；**`[collecting.开始]` / `[collecting.轮次]` / `[collecting.tool执行]` / `[collecting.tool结果]` / `[collecting.写frame]` / `[collecting.结束]` / `[collecting.补救]`**；**`[executor.分发]`** 与各 **`[executor.query_db]`**、**`[executor.submit]`**、**`[executor.submit_contract]`** 等；submit 组装见 **`[pending_submit_v15b]`**（`branch=contract_work_normalized_ok` / `skip_simple_whitelist_*` / `writes_assembled`）；lifespan 已为 **`engine.pending_submit_v15b`** 配 INFO 输出（与 collecting 类似）。确认执行见 **`workflow_engine_exec`**（**`[workflow_engine_exec]`**）。详见 `main.py`、`engine/collecting.py`、`engine/tool_executor.py`、`engine/pending_submit_v15b.py`、`engine/workflow_engine_exec.py`。

**前端**：`frontend/src/components/AiPanel.vue` 在 `confirm_required` 时展示确认卡片；`preview.row`（新建）、`preview.fields`（更新）、删除预览（`operation` / 表名 / id）、**合同**（`preview.contract` + `preview.items` 双表）均可展示；**query** 路径用 `data.main` 渲染表格。**`/ai/confirm` 若 `confirm_result=error`**，按错误样式展示（**error_detail**）。**反馈**：每条 AI 回复可提交 **`POST /ai/feedback`**（见 **「五、数据库」** 中 `business.feedback`）。

### collecting：TOOLS 名与 executor 对应

工具声明存储在 `meta.meta_tools`，由 `load_tools_from_db()` 动态加载。执行分发仍在 `engine/tool_executor.py`：

| 工具名 | tool_type | 执行函数 | 说明 |
|--------|-----------|----------|------|
| `query_db` | read | `execute_query_db` | 只读 SELECT；校验后走 `db_conn()`；未出现 `deleted_at` 时自动包一层 `deleted_at IS NULL`（失败则回退原 SQL） |
| `submit` | composite | `execute_submit` → **pending_submit_v15b** | simple_entity：扁平 data 归一化（**_normalize_simple_entity_work**）；合同：contract+items 归一化（**_normalize_and_validate_contract_work**）；按 **frame.workflow_intent** + **workflow_steps** 组装 **pending_submit.writes** → `pending_confirm` |
| `write_entity` | create | `execute_write_entity` | 新建预览 → `pending_confirm`；不落库；**meta 必填 + allowed_values 校验** |
| `update_entity` | update | `execute_update_entity` | 更新预览 → `pending_confirm`；须 **verified_record_id** |
| `delete_entity` | delete | `execute_delete_entity` | 软删预览 → `pending_confirm`；须 **verified_record_id** |
| `submit_contract` | create_composite | `execute_submit_contract` | 合同主表+明细预览 → `pending_confirm`；**pending_contract**（create_contract 常用） |
| `update_contract` | update_composite | `execute_update_contract` | 合同变更预览 → `pending_confirm`；**pending_contract_update**（兼容；主路径 **`submit`**） |
| `ask_user` | ask | （无 SQL） | 返回追问文案，由 `run_collecting` 写 `reply` |

统一入口：`execute_tool_call(name, arguments)`，由 `run_collecting` 在解析 LLM `tool_calls` 后调用。

**新增工具步骤：**
1. `meta.meta_tools` 插入新行（tool_name / tool_type / description / parameters_schema / validations）
2. `engine/tool_executor.py` 中 `execute_tool_call` 加 elif 分支 + 新的 `execute_xxx` 函数
3. 若以 **submit** 驱动新意图：在 **`business.workflow_steps`** 增加 write 行（**`data_key` / `where_field` / `depends_on`**），并在 **`engine/pending_submit_v15b`** 或 **`engine/workflow_steps_util.infer_write_operation`** 中保证操作类型正确；工具列表仍从数据库加载

### slots：`SLOT_SOURCE_AGENT_PREVIEW`

- 常量定义在 `engine/collecting.py`：`SLOT_SOURCE_AGENT_PREVIEW = "agent_preview"`。
- **`apply_preview_to_slots`**（新建确认前）：写入 `business.slots` 时 **`source = agent_preview`**，供 `_execute_write` 按槽位 INSERT。
- **取消确认**（`POST /ai/confirm`，`confirmed=false`）：除重置 frame、清理 `context_snapshot` 中 `pending_update` / **`pending_delete`** / **`pending_contract`** / **`pending_contract_update`** / `action` 外，执行 **`DELETE FROM business.slots WHERE frame_id = ? AND source = 'agent_preview'`**，只删掉预览写入，**不影响** `user_input`、`system_default` 等来源的槽位。

---

## 四、意图识别（三层架构）

`engine/intent.py`：`detect_intent()` 三层逻辑

- **第一层**：示例句与用户话双向子串匹配（`example_queries`，零向量调用，零延迟）
- **第二层**：Ollama 向量相似度；模型名 `OLLAMA_EMBED_MODEL`（默认 `shaw/dmeta-embedding-zh`）；最高相似度 `>0.85` 且唯一 intent 则直接返回；`<0.60` 则整句不匹配（返回 `None`）；`>0.85` 但多 intent 并列时进入第三层（仅这些并列候选）
- **第三层**：`_detect_intent_dynamic`（LLM JSON）；用户向量获取失败时跳过第二层，对**全部** workflow 调 LLM；否则对第二层给出的候选子集（含上述并列情形，或相似度落在 `[0.60, 0.85]` 的多个 intent）调 LLM。日志前缀：`[intent.层1]` / `[intent.层2]` / `[intent.层3]` / `[intent.最终]`（见 `engine/intent.py`）

**新增 intent 操作步骤：**

1. `business.workflows` 插入新行，写好 `example_queries`（15 条左右）
2. `python scripts/sync_intent_embeddings.py` 重新生成向量
3. 无需改代码，自动生效

**数据库字段：**

```
business.workflows.example_queries   JSONB  示例句列表（人工维护）
business.workflows.query_embeddings  JSONB  向量列表（sync 脚本维护，勿手动改）
```

**环境依赖：**

- `OLLAMA_EMBED_MODEL=shaw/dmeta-embedding-zh`（已在 `.env`）
- `numpy` 已加入 `requirements.txt`

---

## 五、数据库

```
数据库：PostgreSQL，库名 aierp

Schema 分区：
    business    业务主表
    meta        元数据（llm_assistant 角色只读）
    ai_assistant
    public

连接池（tools/database.py）：
    db_conn()       API 用，用户来自环境变量 PGUSER（默认 nicjiang）
    db_conn_llm()   LLM/工作流用，用户 llm_assistant，密码 llm_safe_2024

环境变量：
    PGDATABASE      默认 aierp
    PGUSER          默认 nicjiang
    PGHOST          默认 localhost
    PGPORT          默认 5432
    PG_POOL_MAX     默认 20
```

### 主要业务表

```
business.customers              客户主数据
business.contacts               外部联系人（客户/供应商）
business.products               产品主数据；名称列 **`product_name`**（已由 `name` 更名，与 `contract_items.product_name` 及 LLM 习惯一致）
business.contract_templates     合同条款模版（内贸/外贸）
business.contracts              合同主表，自动编号 CT-YYYYMM-001
business.contract_items         合同子表，金额自动计算并汇总回主表
business.contract_changes       合同变更证据链（`snapshot_before` / `snapshot_after`、`reason`、`change_type` 等；与 `constitution/add_contract_changes.sql` 一致）
business.workflows              工作流定义（intent / display_name / description / example_queries / query_embeddings）
business.workflow_steps         collecting 步骤（lookup / collect / **write** 等）；V15 起 write 步含 **data_key**（映射 **submit** 的 `data` 键）、**where_field**（软删 WHERE 列）、**depends_on**（前序 **seq**）；迁移见 **v15b / v15c / v15d** SQL
business.sessions               对话会话，2 小时无活动自动开新 session
business.frames                 任务实例（原 workflow_tasks），含 subject / context_snapshot；**`status` 约束仅允许 `collecting` / `completed` / `cancelled`**（**禁止使用 `superseded`**，否则违反 CHECK；清理或 supersede 时请用 `cancelled` 等合法值）
business.slots                  参数槽位（原 workflow_task_params），含 value_raw / confidence / source（填槽时联合 meta_fields.allowed_values）
business.turns                  对话轮次，每轮 user / assistant 各一条
business.feedback               用户反馈（会话/帧快照、最近 turns、可选备注）；建表见 `constitution/add_feedback.sql`
meta.meta_tables                表目录（table_cn / description / nav_section / entity_type 等）
meta.meta_fields                字段清单，由 sync_constitution() 全量重建，field_cn 由 LLM 生成
meta.meta_tools                 工具声明表：tool_name / tool_type / description / parameters_schema / validations / confirm_required / enabled / sort_order
```

---

## 六、LLM 配置

```
主力模型（本地，Ollama OpenAI 兼容）：
    LLM_BASE_URL=http://localhost:11434/v1
    LLM_MODEL_NAME=gemma4:26b        # 当前约定主力模型；可按机器改为其它已拉取的 Ollama 模型名
    LLM_API_KEY=ollama

可选降级 / 云端（OpenAI 兼容，备用）：
    FALLBACK_BASE_URL=https://api.deepseek.com/v1
    FALLBACK_MODEL_NAME=deepseek-chat
    FALLBACK_API_KEY=（见 .env）
```

**当前仓库 `.env` 若以 DeepSeek 为主力**（如 `LLM_MODEL_NAME=deepseek-chat`）：e2e 可在该配置下验证；切回本地 **`gemma4:26b`** 请改 `LLM_*` 三行（见 `.env` 内注释），function calling 稳定性可能与云端不同。

**collecting 与模型分流**：`run_collecting` 及链路上的 `chat_with_tools` **`prefer_fallback=False` 写死**，**无**「本地失败再切云端」等分流逻辑；意图识别与其它步骤均走 **`LLM_*`**。其它代码路径若需云端，仅在对应调用处显式 `prefer_fallback=True`（需配置 `FALLBACK_*`），**与 collecting 无关**。

---

## 七、前端表格渲染与交互

- 放弃 `n-data-table`，改用原生 HTML `<table>`
- `data.main` → 主查询结果表格
- `data.sub` → 关联明细表格（如合同子项）
- `confirm_required` → 确认卡片（说明 + 字段预览表 + 确认/取消）；确认/取消请求 **`POST /ai/confirm`**；响应 **`confirm_result`**：`success` | `error` | `cancelled`；失败时读 **`error_detail`**
- 渲染逻辑在 `frontend/src/components/AiPanel.vue`
- **用户反馈**：助手消息旁 **反馈** 按钮 → **`POST /ai/feedback`**（`session_id` / `frame_id` 来自最近一次 `/ai/chat` JSON 的 **`session_id`**、**`frame_id`**；流式 SSE 路径可能无 `session_id`，上下文会偏少）
- **开发者查反馈**：`SELECT * FROM business.feedback WHERE status = 'open' ORDER BY created_at DESC;`

---

## 八、启动方式

```bash
# 一键启动前后端
./dev.sh

# 仅后端
.venv/bin/python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000

# 仅前端
cd frontend && npm run dev

# 同步元数据字段中文名
python scripts/sync_meta_cn.py

# 同步意图向量（新增 intent 后执行）
python scripts/sync_intent_embeddings.py

# 新环境建 meta_tools 表
psql -d aierp -f constitution/create_meta_tools.sql

# 用户反馈表（若尚未执行）
psql -d aierp -f constitution/add_feedback.sql

# V15 workflow_steps 扩展（按环境按需执行，未执行则 runtime 有降级）
# psql -d aierp -f constitution/v15b_workflow_steps.sql
# psql -d aierp -f constitution/v15c_workflow_steps.sql
# psql -d aierp -f constitution/v15d_update_contract_steps.sql

# 工具执行层防护回归（不调用 LLM，需 DB）
.venv/bin/python scripts/test_executor_guards.py

# 端到端 HTTP（需后端 127.0.0.1:8000 与可用 LLM；单次请求超时可用 E2E_CHAT_TIMEOUT）
# 建议加 PYTHONUNBUFFERED=1，便于日志与 print 实时输出
PYTHONUNBUFFERED=1 .venv/bin/python scripts/test_e2e_chat.py
```

**PostgreSQL（Homebrew）**：若 **`Connection refused`** 或 **`postmaster.pid` / lock** 报错（如异常断电），可先 **`brew services restart postgresql@17`**；仍失败则在 **确认无 postgres 进程** 后清理数据目录下陈旧 **`postmaster.pid`** 再启动（勿在实例运行时删锁）。

---

## 九、context 约定

所有工作流函数统一接收并返回 `context` 字典，LLM 和代码共享同一个 context，禁止在函数内部建立独立状态。

```python
# 标准签名
def some_tool(context: dict) -> dict:
    ...
    context["output_key"] = result
    return context
```

---

## 十、待办（当前优先级）

**已完成：**

- ✅ **collecting 侧 CRUD 完成度（HTTP 主路径）**：主数据查询 **Query**（`query_master_data`）、简单实体 **Create / Update**（`create_simple_entity` / `update_simple_entity`）、**Delete**（`delete_simple_entity` + 软删确认）、**Create Contract**（`create_contract` + `submit_contract` + 跨表事务确认写入）
- ✅ 三层意图识别（子串匹配 → 向量 → LLM）
- ✅ `query_master_data`：与 CUD 同属 **collecting**；`query_db` + `execute_query_db` + **`data.main`** 表格；意图仍登记在 `business.workflows`，**不再走** `run_workflow` 预置 SQL 模板
- ✅ 前端表格渲染（原生 HTML `table`，data.main / data.sub）
- ✅ 向量字段入库，sync 脚本，新增 intent 无需改代码
- ✅ `create_simple_entity` collecting：LLM tool 循环、`/ai/confirm`、预览 slots（`agent_preview`）与取消清理；代码预填强制 `write_entity`；与 update 同为本地单模型
- ✅ `update_simple_entity` collecting：强 prompt + `update_entity` 与 `verified_record_id`、`pending_update`、确认后 `_execute_update`
- ✅ `tool_executor`：`write_entity`/`update_entity`/`query_db` 防护；确认后 `_execute_write`/`_execute_update` 与 `execution_result`
- ✅ `scripts/test_executor_guards.py` / `scripts/test_e2e_chat.py` 回归与链路抽检
- ✅ sessions / frames / slots / turns 四表地基
- ✅ allowed_values 枚举约束（旧版 fill_slots / check_and_proceed 仍保留在文件中，主路径为 agent 循环）
- ✅ 工具声明层数据库化：`meta.meta_tools` 表 + `load_tools_from_db()` 动态加载，collecting 已切换为数据库驱动
- ✅ 业务表 **软删除** `deleted_at`（`constitution/add_deleted_at.sql`）；`delete_entity` + **`delete_simple_entity`** collecting 与 `/ai/confirm` 软删执行
- ✅ **`create_contract` + `submit_contract` / `submit`**：collecting、预览 **`pending_contract`** 或 **`pending_submit`**；确认 **`run_confirm_generic_writes`** 写入 `contracts` + `contract_items`；编号由触发器生成；**`/ai/chat` 始终 `detect_intent`**，与旧 collecting frame **意图不一致则 supersede**（`cancelled`）；前端合同确认双表预览
- ✅ **products** 表 **`name` → `product_name`**（与 `contract_items` 及 LLM 习惯一致；`constitution/rename_products_name_to_product_name.sql`）
- ✅ **V15 submit / 确认引擎（V15-C/D）**：**`execute_submit`** → **pending_submit_v15b** + **`workflow_steps`**（**data_key / where_field**）；**`run_confirm_generic_writes`** 统一执行；**`data_sanitize`**；**`update_contract`** 主路径 **`submit`** + **v15d** 多 write 步；**`commit_update_contract`** 已移除
- ✅ **合同 Update / Delete + `contract_changes`**：**`delete_contract`** 多 write + **`contract_changes`**；**`update_contract`** 与 **`delete_contract`** 的变更记录均走通用引擎；**`execute_update_contract`** 仅工具兼容；取消确认时清理 **`pending_contract_update` / `pending_submit`**
- ✅ **链路可观测性**：结构化日志前缀（`[chat.*]`、`[confirm.*]`、`[collecting.*]`、`[executor.*]`、`[execute.*]`、`[intent.*]` 等），便于排查
- ✅ **`/ai/confirm` 错误透传**：`confirm_result` + `error_detail`；前端错误样式区分
- ✅ **用户反馈**：`business.feedback` + **`POST /ai/feedback`** + AiPanel 反馈按钮；**`/ai/chat`** 工作流 JSON 返回 **`session_id`**、**frame_id**
- ✅ **V16 字段裁剪与 ask_if_missing 校准**：`sync_workflow_step_fields` 对 collect/write 步骤只注入 `ask_if_missing=true` 字段；`meta.v_field_responsibility` 审核视图
- ✅ **V16 工具名统一**：prompt_rules / allowed_tools / workflow_steps.instruction 收敛为 query_db / submit / ask_user（LLM 侧）
- ✅ **V16 submit 归一化**：simple_entity 扁平数据处理 + 合同 contract+items 归一化 + 必填校验改用 **is_required**
- ✅ **V16 合同写入白名单 + depends 修复**：contracts/contract_items 纳入 submit 组装与 confirm 执行路径；**contract_id** 依赖传递修正
- ✅ **V16 session 隔离**：`/ai/chat` 支持客户端指定 **session_id**；e2e 每场景独立 session
- ✅ **V16 e2e 测试稳定化**：场景 6 合同多轮补全 + **库校验断言** + 软删清理；场景 6 合同创建可稳定 PASS（其余场景仍受 LLM 随机性影响）

**待办：**

```
🟡 memo 机制 + exploring frame（自由对话架构基础）
🟡 main.py 模式分流（探索模式 vs 执行模式）
🟡 input_fields / output_fields 补齐（memo → collecting 字段映射）
🟡 代码整合清理（合并分散的引擎文件，删除旧函数冗余代码）
🟡 update_contract v15d 的 5 个 write 步骤补入 workflow_steps
🟡 collecting 与 turns 历史对齐的细节优化
🟡 指代消解修复
🟡 update_master_data workflow（非 simple 实体批量改）
🟢 信息聚合查询（如「这个客户的历史合同」，验证 query_db 跨表能力）
🟢 沙箱初步设计（用户通过自然语言新增字段/表，操作 meta_tables / meta_fields）
🟢 query_complex（跨表联合查询，LLM 动态生成 SQL）
🟢 权限控制，user_id 硬编码待替换
🟢 tool_executor 校验规则引擎化（读 meta_tools.validations 驱动通用校验，时机：新增第 5 个工具时）
```
