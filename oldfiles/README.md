# AI·ERP 仓库说明（由代码扫描生成）

**PM / 非技术读者请先读：** [`docs/PM_项目地图.md`](docs/PM_项目地图.md)（业务主流程与协作说明）。  
**研发主路径清单：** [`docs/技术主路径与整理清单.md`](docs/技术主路径与整理清单.md)。架构与细节以根目录 **`CLAUDE.md`** 为准。

---

说明：下列内容仅依据仓库内可见文件与代码，不含推测性描述。目录清单来自 `find` 排除 `.venv`、`node_modules`、`.git` 后的路径（含 `__pycache__`、`dist` 等生成物；若不需要可自行忽略）。

---

## 1. 目录与文件（路径 → 作用）

### 仓库根

| 路径 | 作用 |
|------|------|
| `main.py` | FastAPI 应用：表 CRUD、导航、xlsx 导出、`/ai/chat` 等 HTTP API |
| `requirements.txt` | Python 依赖列表 |
| `dev.sh` | 并行启动根目录 uvicorn（8000）与 `frontend` 的 `npm run dev` |
| `db_pool.py` | 文件头写明「已迁移至 tools/database.py，待确认后删除」；内含 `psycopg2.pool.ThreadedConnectionPool` 双池与 `db_conn`/`db_conn_llm` |
| `llm_client.py` | 文件头写明「已迁移至 llm/client.py，待确认后删除」；OpenAI 兼容客户端与 `parse_file` 等 |
| `llm_tools.py` | 文件头写明「已迁移至 llm/tools.py，待确认后删除」；从 `db_pool` 导入的连接版 LLM 工具链 |
| `.env` | 本地环境变量（`.gitignore` 忽略；内容未写入本文档） |
| `.gitignore` | Git 忽略规则 |
| `.memsearch.toml` | 被 `.gitignore` 引用忽略的配置文件（若存在） |
| `AI·ERP系统设计概要.txt` | 设计概要文本 |
| `常用命令.txt` | 人工整理的常用命令说明（与代码路径不完全一致处以仓库实际路径为准） |

### `constitution/`（SQL）

| 路径 | 作用（摘自文件头注释） |
|------|-------------------------|
| `add_nav_columns_meta_tables.sql` | 为 `meta.meta_tables` 增加导航字段（`nav_section`/`nav_order`/`nav_parent_table`）及种子数据 |
| `create_auth_tables.sql` | 登录与权限测试数据：`business.user_accounts`、`business.user_role_grants` 等 |
| `create_design_memories.sql` | 建 `business.design_memories`（设计/架构过程记忆，768 维向量等） |
| `create_memories_contracts.sql` | 建 `business.memories` 与 `business.contracts` |
| `register_auth_migration_procedure.sql` | 将认证迁移登记到 `business.procedures`（PROC-002），含可 `CALL` 的提示过程 |
| `register_sync_constitution_procedure.sql` | 登记 `meta.sync_constitution()` 到 `business.procedures` 并写 `procedure_logs` |
| `setup_llm_user.sql` | 初始化 LLM 助手数据库角色/权限（注释含 `\connect aierp`） |
| `sync_constitution.sql` | 定义 `meta.sync_constitution()`：重建/同步 `meta_fields`、`meta_tables`（见文件内长注释） |
| `add_workflow_intent_embeddings.sql` | 为 `business.workflows` 增加 `example_queries` / `query_embeddings`（`ADD COLUMN IF NOT EXISTS`） |
| `update_meta_catalog_grid_20250325.sql` | 设计记忆入库 + 更新 `meta_tables`/`meta_fields`（与 TableView/导出等联动说明） |

### `engine/`

| 路径 | 作用 |
|------|------|
| `__init__.py` | 包说明字符串：工作流执行引擎 |
| `workflow.py` | 从 `business.workflow_steps` 读步骤并执行（`query_sql`/`code`/`llm`） |
| `workflow_runner.py` | 将 workflow 结果转为聊天响应；含 `run_query_master_data`、`execute_workflow_for_chat` 等 |
| `intent.py` | 从 `business.workflows` 做意图识别、`extract_query_params`、`explain_capabilities`（与 `llm/intent.py` 不同文件） |

### `llm/`

| 路径 | 作用 |
|------|------|
| `__init__.py` | 包说明字符串 |
| `client.py` | OpenAI 兼容 LLM：`chat`、`chat_stream`、`chat_expert_only`、`chat_with_fallback`、`parse_file` 等；加载项目根 `.env` |
| `tools.py` | `query_db`/`execute_db` 工具循环、挂起确认、`chat_with_db_outcome` 与流式变体 |
| `intent.py` | 占位：`identify_intent`、`check_fields`、`ask_user`、`parse_reply` 空实现 |

### `tools/`

| 路径 | 作用 |
|------|------|
| `__init__.py` | 导出 `TOOLS` 字典与工作流可调函数 |
| `database.py` | **实际使用的**双连接池（`ThreadedConnectionPool`）、`db_conn`/`db_conn_llm`、`query_table`/`write_table`/`update_table`、元数据读取 |
| `file_reader.py` | `read_pdf`/`read_excel`/`read_image` 占位（返回 context 拷贝） |
| `file_writer.py` | `export_excel`/`generate_pdf` 占位 |

### `scripts/`

| 路径 | 作用 |
|------|------|
| `fill_meta_fields_menu_labels.py` | 为 `meta.meta_fields` 填充菜单标签相关逻辑 |
| `seed_create_contract_workflow.py` | 向库中种子化 create_contract 工作流 |
| `sync_meta_cn.py` | 批量调 LLM 填 `field_cn` 等（与 `sync_constitution.sql` 注释呼应） |
| `test_query_master_data.py` | 测试 `run_query_master_data` |
| `test_workflow.py` | 测试 `run_workflow`（含库内步骤备份/恢复辅助函数） |
| `test_intent_and_workflow.py` | 脚本级调用 `detect_intent` 与 `execute_workflow_for_chat`（无 `def main`） |
| `sync_intent_embeddings.py` | 执行 `add_workflow_intent_embeddings.sql` 与 `seed_workflow_example_queries.sql` 后，将 `example_queries` 逐句向量化写入 `query_embeddings`（Ollama，`OLLAMA_EMBED_MODEL`） |
| `seed_workflow_example_queries.sql` | 为部分 `intent` 写入/更新 `example_queries` 示例句（由 `sync_intent_embeddings.py` 调用） |
| `upsert_design_memory.py` | 设计记忆 upsert 命令行入口 |

### `frontend/`

| 路径 | 作用 |
|------|------|
| `package.json` | Vue3 + Vite + vue-router + pinia + naive-ui + sortablejs |
| `vite.config.js` | Vite：`@vitejs/plugin-vue`、`vite-plugin-vue-devtools`、`@` → `./src` |
| `index.html` | 入口：`<script type="module" src="/src/main.js">` |
| `src/main.js` | `createApp`、Pinia、Router、挂载 `#app` |
| `src/App.vue` | 布局：`NavPanel` + `<RouterView />` + `AiPanel` |
| `src/router/index.js` | 路由：`/dashboard`、`/meta`、`/meta/:table`、`/business`、`/business/:table`、`/ai-rules` |
| `src/stores/app.js` | Pinia store：API 基址为空（同域），请求 tables/nav/columns/CRUD/导出 |
| `src/assets/main.css` | 全局样式（由 `main.js` 引入） |
| `src/components/NavPanel.vue` | 侧栏导航、探测后端 `/`、拉取 tables/nav |
| `src/components/DataNavPanel.vue` | 数据区导航（被 NavPanel 使用） |
| `src/components/AiPanel.vue` | AI 对话面板（调用 `/ai/chat` 等，细节见组件内代码） |
| `src/views/DashboardView.vue` | 仪表盘：展示 meta/business 表链接 |
| `src/views/MetaView.vue` | 元数据分区视图 |
| `src/views/BusinessView.vue` | 业务数据分区视图 |
| `src/views/TableView.vue` | 通用表数据视图 |
| `src/views/AiRulesView.vue` | AI 规则视图 |
| `src/utils/tableExport.js` | `buildExportXlsxUrl`：拼后端 `GET /export/xlsx/...` |
| `main.css` | 根目录样式文件（与 `src/assets/main.css` 并存） |
| `main.js` | 根目录 JS（与 `src/main.js` 不同路径） |
| `main.py` | **另一套** FastAPI：`psycopg2.connect` 直连、Anthropic API 的 `/ai/chat`（与根目录 `main.py` 不是同一应用） |
| `App.vue`、`AiPanel.vue`、`DashboardView.vue`、`NavPanel.vue`、`TableView.vue`、`BusinessView.vue`、`MetaView.vue`、`AiRulesView.vue`（根目录） | 与 `src/` 下同名文件并存；**当前 Vite 入口未引用这些根目录 .vue**（以 `index.html` 为准） |
| `app.js` / `index.js` | 根目录 JS 文件（非 Vite 当前入口） |
| `dist/` | `vite build` 构建输出（html/js/css） |
| `public/favicon.ico` | 静态资源 |
| `README.md` | 前端子项目自述（若存在） |
| `jsconfig.json` | JS 工程配置 |
| `.vscode/extensions.json`、`.vscode/settings.json` | VS Code 工作区配置 |
| `.gitignore` | 前端子目录忽略规则 |

### 生成物 / 杂项

- 各处 `__pycache__/*.pyc`：Python 字节码缓存。
- `.DS_Store`：macOS 文件夹元数据。

---

## 2. 各文件中的函数 / 类（一句话）

### `main.py`

- `_env_bool`：从环境变量解析布尔。
- `_cors_allow_origins_and_credentials`：计算 CORS 白名单与是否允许 credentials。
- `_app_lifespan`：应用生命周期结束时 `close_all_pools()`。
- `_prepare_user_text_for_intent_regex`：NFKC + 遮蔽否定删除子串供意图检测。
- `_assert_tool_was_called`：用户话像数据库操作时若模型未调工具则抛错。
- `assert_ident`：校验标识符正则，不合法则 HTTP 400。
- `fetch_column_meta`：读 `information_schema.columns`。
- `writable_columns` / `writable_names`：过滤可写列（排除向量、生成列等）。
- `coerce_cell`：请求体值转为可写入 psycopg2 的类型。
- `table_has_id`：元数据中是否存在 `id` 列。
- `filterable_column_names`：可做模糊筛选的列名。
- `_like_escape`：LIKE 转义。
- `parse_filters_query`：解析查询参数 `filters` JSON。
- `build_fuzzy_where_clause`：拼 ILIKE WHERE 片段与参数。
- `_order_expr_for_column`：排序表达式（向量列转 text）。
- `build_order_clause`：生成 `ORDER BY` 子句。
- `normalize_sort_order_param`：规范化 asc/desc。
- `fetch_table_page`：分页查表（可选 total）。
- `root`：健康检查 JSON。
- `list_tables`：列出 meta/business/ai_assistant 基表。
- `_schema_by_table_name`：表名到 schema 映射。
- `list_nav_tables`：按 `meta.meta_tables.nav_section` 分组导航。
- `get_table_column_labels`：读 `meta.meta_fields` 列展示名。
- `get_table`：分页+排序+筛选查表。
- `_export_cell_value` / `_resolve_export_columns`：xlsx 导出单元格与列选择。
- `export_table_xlsx`：返回 xlsx 二进制响应。
- `table_insert` / `table_update` / `table_delete`：通用 CRUD。
- `_sse_delta_payload`：SSE 增量帧字节。
- `ChatMessage` / `ChatRequest`：Pydantic 请求体。
- `get_llm_db_config`：返回 LLM 工具用的 PG 连接信息字典（环境变量默认值见代码）。
- `_db_chat_system_prompt` / `_db_chat_after_operation_user_confirm_prompt`：拼接系统提示。
- `ai_chat`：`POST /ai/chat` 处理（workflow 分支、DB 工具链、SSE）。

### `tools/database.py`

- `_api_pg_kwargs` / `_llm_pg_kwargs`：两套 psycopg2 连接参数。
- `get_conn` / `release_conn` / `get_conn_llm` / `release_conn_llm`：从池取还连接。
- `db_conn` / `db_conn_llm`：上下文管理器包装取还连接。
- `close_all_pools`：关闭两个池。
- `get_meta_tables` / `get_meta_fields`：读 meta 目录。
- `_single_sql`：拒绝多语句。
- `query_table` / `write_table` / `update_table`：工作流用 context SQL 执行封装。

### `db_pool.py`（遗留副本）

- 与 `tools/database.py` 同构的池与 `db_conn`/`db_conn_llm`/`close_all_pools`（见文件内注释）。

### `engine/workflow.py`

- `WorkflowStepError`：步骤失败异常（带步骤元数据）。
- `_single_sql`、`_substitute_template`、`_run_query_sql`、`_run_code_step`、`_run_llm_step`、`_fetch_steps`、`_ensure_workflow_exists`、`_normalize_on_fail`：步骤执行内部逻辑。
- `run_workflow`：按 intent 执行 workflow 步骤并写回 context。

### `engine/workflow_runner.py`

- `_validate_ident`、`_like_escape`、`_normalize_conditions_for_run`、`_stem_table`、`_discover_sub_tables`、`_parent_fk_column`：查询辅助。
- `_summarize_query_narrative`：LLM 润色查询结果叙述。
- `_persist_workflow_task`：写 `workflow_tasks` / `workflow_task_params`。
- `run_query_master_data`：主数据查询工作流函数。
- `_collect_execution_facts`、`_infer_status_hint`、`_llm_summarize_workflow_result`、`_fallback_plain_summary`、`_normalize_workflow_data_payload`：总结与包装。
- `execute_workflow_for_chat`：对外统一返回字典给 HTTP。

### `engine/intent.py`

- `_parse_llm_json_object`：剥 markdown 围栏后 `json.loads`。
- `_normalize_conditions_from_llm`：规范化 conditions 列表。
- `extract_query_params`：LLM 抽取表名与查询条件。
- `_load_workflows`：读 `business.workflows`（含 `example_queries`、`query_embeddings`）。
- `_parse_example_queries_field` / `_parse_query_embeddings_field`：解析 JSON 字段。
- `_layer1_substring_match`：第一层，示例句与用户话双向子串匹配。
- `_get_embedding`：Ollama `embed`/`embeddings`，模型 `OLLAMA_EMBED_MODEL`（默认 `shaw/dmeta-embedding-zh`）。
- `_layer2_embedding_scores`：与各 intent 示例向量比余弦，排序。
- `_resolve_intent_fuzzy`：第三层 LLM 输出与库中 intent 的保守对齐（别名、前缀等）。
- `_detect_intent_llm`：第三层，仅候选 workflow 上调 `chat`/`chat_expert_only`，返回前剥离 `intent=` 等前缀。
- `detect_intent`：三层总入口（见下文「意图识别」）。
- `explain_capabilities`：无匹配时的引导回复。

### `llm/client.py`

- `_load_dotenv_if_available`、`_env_str`、`_env_llm_api_key`：环境与配置。
- `llm_openai_extra_body`：可选 `reasoning_effort`。
- `_connection_hint`：连接错误提示文案。
- `chat` / `chat_stream` / `chat_expert_only` / `chat_with_fallback` / `chat_stream_with_fallback`：LLM 调用。
- `parse_file`：解析 `filename|base64` 上传（图片/Excel/PDF）。

### `llm/tools.py`

- 大量以下划线开头的校验/调度/流式合并函数（SQL 校验、DELETE/DROP 目标解析、挂起字典、OpenAI tool 循环等）。
- `DbStreamPendingMarker`：流式 pending 标记 dataclass。
- `chat_with_db_outcome_stream` / `resume_pending_db_chat_stream`：流式 DB 对话。
- `chat_with_db` / `DbChatOutcome` / `chat_with_db_outcome` / `resume_pending_db_chat`：同步语义与 pending 恢复。

### `llm/intent.py`

- `identify_intent` / `check_fields` / `ask_user` / `parse_reply`：占位返回空或原 context。

### `tools/__init__.py`

- `query_master_data`：惰性导入 `run_query_master_data`。
- `TOOLS`：名称到可调用工具的字典。

### `tools/file_reader.py` / `file_writer.py`

- `read_pdf` / `read_excel` / `read_image` / `export_excel` / `generate_pdf`：占位返回 context。

### `llm_client.py` / `llm_tools.py`（遗留）

- 与 `llm/client.py`、`llm/tools.py` 对应内容的老路径版本（`llm_tools` 仍 `from db_pool import db_conn_llm`）。

### `frontend/src/stores/app.js`

- `useAppStore`：Pinia store，内含 `fetchTables`、`fetchNavTables`、`fetchColumnLabels`、`fetchTableData`、`tableInsert`、`tableUpdate`、`tableDelete`、`downloadTableXlsx`、`_errDetail` 等。

### `frontend/src/utils/tableExport.js`

- `buildExportXlsxUrl`：构造导出 URL。

### `frontend/main.py`

- `_cors`：CORS 配置元组。
- `get_conn`：`psycopg2.connect` 固定参数。
- `root` / `list_tables` / `get_table`：简化只读 API。
- `ChatMessage` / `ChatRequest`：聊天请求模型。
- `ai_chat`：转发 Anthropic Messages API。

### `scripts/`（主要入口与辅助）

- `fill_meta_fields_menu_labels.py`：`has_cjk`、`first_cjk_phrase`、`humanize_en`、`menu_cn`、`menu_en`、`main`。
- `seed_create_contract_workflow.py`：`_conn_kwargs`、`main`。
- `sync_meta_cn.py`：`_parse_json_array`、`_fetch_batch`、`_build_prompt`、`main`。
- `test_workflow.py`：多个 `_` 前缀辅助函数与 `main`。
- `test_query_master_data.py`：`_print_case_header`、`_print_result`、`_audit_workflow_tables`、`main`。
- `upsert_design_memory.py`：`parse_args`、`main`。
- `test_intent_and_workflow.py`：顶层脚本逻辑，无函数定义。
- `sync_intent_embeddings.py`：执行 DDL/种子 SQL、批量 embedding 写库（顶层脚本）。

### Vue 单文件组件（`script setup`，无导出类）

- `src/App.vue`：壳布局。
- `src/components/NavPanel.vue`：侧栏与 API 状态。
- `src/components/DataNavPanel.vue`：数据导航树/列表（实现见文件）。
- `src/components/AiPanel.vue`：聊天 UI 与请求。
- `src/views/DashboardView.vue`：仪表盘统计与表链接。
- `src/views/MetaView.vue`、`BusinessView.vue`、`TableView.vue`、`AiRulesView.vue`：各路由页面 UI（实现见文件）。

---

## 3. 数据库连接方式

- **HTTP API 主路径**（`main.py` 使用 `from tools.database import close_all_pools, db_conn`）：`psycopg2.pool.ThreadedConnectionPool`，封装在 `tools/database.py`。
  - 连接参数来源环境变量：`PGDATABASE`（默认 `aierp`）、`PGUSER`（默认 `nicjiang`）、`PGHOST`（默认 `localhost`）、`PGPORT`（默认 `5432`）；**未在 `_api_pg_kwargs` 中设置 password**（由 libpq 默认机制处理）。
  - 池大小：`PG_POOL_MAX`（默认 20），最小连接数 1。
- **LLM 工具 / 工作流**（`db_conn_llm()`）：**第二个** `ThreadedConnectionPool`，默认用户 `llm_assistant`、默认密码 `llm_safe_2024`（环境变量 `PGUSER`/`PGPASSWORD` 可覆盖，与 `main.get_llm_db_config()` 注释一致）。
  - 池大小：`PG_POOL_MAX_LLM`（默认同 `PG_POOL_MAX`）。
- `main.py` 中 `get_llm_db_config()` 返回给 `chat_with_db_outcome` 等的字典**不用于** `llm/tools.py` 里实际建连；`llm/tools.py` 注释写明执行使用 `db_conn_llm()`。
- 遗留文件 `db_pool.py` 实现与 `tools/database.py` 同型双池；根目录 `frontend/main.py` 使用 **`psycopg2.connect` 单次连接**（硬编码 `dbname/user/host/port`），与主后端不同。

---

## 4. 意图识别（`detect_intent`，与 `engine/intent.py` 实现一致）

- **第一层**：`_layer1_substring_match`，对 `business.workflows.example_queries` 中每条示例句与用户输入做双向子串（`ex in user` 或 `user in ex`），命中则直接返回对应 `intent`。
- **第二层**：用户话量化为向量（`ollama` 包，`OLLAMA_EMBED_MODEL`，默认 `shaw/dmeta-embedding-zh`）；与各 intent 的 `query_embeddings` 比余弦相似度，常量 `_EMBED_HIGH=0.85`、`_EMBED_LOW=0.60`。
  - 若**无法**得到用户向量：打日志后**跳过**第二层，第三层对**全部** workflow 调 LLM。
  - 若最高相似度 **`>0.85`** 且在容差内**唯一** intent：直接返回该 intent。
  - 若最高相似度 **`>0.85`** 但**多个** intent 并列：第三层仅在这些并列 intent 上调 LLM。
  - 若最高相似度 **`<0.60`**：不匹配，返回 `None`（不调第三层）。
  - 若最高相似度在 **`[0.60, 0.85]`**：第三层携带所有相似度 `≥0.60` 的 intent 作为候选调 LLM。
- **第三层**：`_detect_intent_llm`，仅在候选 workflow 列表上请求 LLM 返回 intent 或 `null`；`use_expert=True` 时走 `chat_expert_only`。返回串会先去掉引号及 `intent=` / `意图：` 等前缀，再与库名精确匹配，必要时 `_resolve_intent_fuzzy`。

**新增 intent（数据与向量）**

1. 在 `business.workflows` 插入或更新行，填写 `example_queries`（约 15 条）。
2. 运行 `python scripts/sync_intent_embeddings.py`（会执行 `constitution/add_workflow_intent_embeddings.sql`、种子 SQL，并写回 `query_embeddings`）。
3. 无需改 Python 业务代码即可生效。

**环境**：`OLLAMA_EMBED_MODEL`（默认 `shaw/dmeta-embedding-zh`，可与 `.env` 一致）；需本机 Ollama 已拉取对应模型。

---

## 5. 前端框架与主要页面

- **框架**：Vue 3 + **Vite 7** + **vue-router 5** + **Pinia 3** + **naive-ui** + **sortablejs**（见 `frontend/package.json`）。
- **路由与页面**（`src/router/index.js`）：
  - `/` → 重定向 `/dashboard`
  - `/dashboard` → `DashboardView.vue`（标题 meta：仪表盘）
  - `/meta` → `MetaView.vue`（元数据）
  - `/meta/:table` → `TableView.vue`
  - `/business` → `BusinessView.vue`（业务数据）
  - `/business/:table` → `TableView.vue`
  - `/ai-rules` → `AiRulesView.vue`（AI 规则）
- **全局布局**：`App.vue` 中左侧 `NavPanel`、中间 `RouterView`、右侧 `AiPanel`。
- **API 基址**：`src/stores/app.js` 与 `src/utils/tableExport.js` 中为空字符串（同域；本地开发可由代理指向后端）。

---

## 6. 启动方式（代码/脚本中实际出现的命令）

- **一键**：仓库根目录 `./dev.sh`  
  - 使用 `.venv/bin/python`（若不存在则打印提示并退出）。  
  - 执行：`.venv/bin/python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000`  
  - 子 shell：`cd frontend && npm run dev`  
  - `wait` 等待子进程；`INT/TERM` 时 `kill` 后台任务。
- **仅后端**（`常用命令.txt` 与 `dev.sh` 一致）：在项目根目录  
  `.venv/bin/python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000`  
  （注释要求工作目录为仓库根，以便加载本仓库 `main.py`。）
- **仅前端**：`cd frontend && npm run dev`（Vite 开发服务器；具体端口以终端输出为准，CORS 白名单含 5173）。
- **Python 依赖**：`pip install -r requirements.txt`（`dev.sh` 提示用 `python3 -m venv .venv` 创建环境）。

---

## 附：根目录 `main.py` 提供的 HTTP 路径（便于对照）

- `GET /`、`GET /tables`、`GET /nav/tables`、`GET /table-columns/{table_name}`
- `GET /table/{schema}/{table_name}`、`GET /export/xlsx/{schema}/{table_name}`
- `POST/PATCH/DELETE /table/...`（插入/更新/删除）
- `POST /ai/chat`

（`frontend/main.py` 为独立应用，不在 `dev.sh` 中启动。）
