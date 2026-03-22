# AIERP 系统设计文档
> AI驱动业务逻辑的轻量ERP · v0.3

---

## 1. 系统定位

从零开始，用AI驱动业务逻辑的轻量ERP。

**核心思想：规则在数据库里，不在代码里。业务变了，改宪法表，不改代码。**

**商业模式**：软件免费，Token收费。聚合多个AI模型接入，平台抽佣。面向所有贸易公司，底座通用，宪法表内容按行业定制。

> 💡 轻量的边界：技术架构轻量（无需专职DBA），初期单租户，规模化后扩展为多租户。

---

## 2. 整体架构

系统分为五层：

| 层级 | Schema | 名称 | 作用 |
|------|--------|------|------|
| 第一层 | meta | 宪法表（4张） | AI理解业务的规则基础，只读 |
| 第二层 | meta | 主数据表（5张） | AI认识人、产品、组织 |
| 第三层 | business | 业务表（7张） | AI建立事件、跟进、锁定 |
| 第四层 | business | 记忆层 | 集中存储语义内容和向量索引 |
| 第五层 | — | 文件存储 | 原始文件实体（PDF、图片、邮件等）|

---

## 3. 宪法表（Constitution Layer）

> 系统的基本法，所有AI行为的规则来源。AI只读，不可修改。

| 中文名 | 数据库名称 | 内容 | 目的 |
|--------|-----------|------|------|
| 表单一览 | `meta.meta_tables` | 所有表的集合 | AI匹配意图，找到目标表 |
| 字段明细 | `meta.meta_fields` | 每张表的字段解释和关系 | AI准确写出SQL，不报错 |
| 事件规则 | `meta.meta_rules` | 所有事件类型的定义，含唯一性条件 | AI判断事件类型，决定归入还是新建 |
| 步骤模板 | `meta.meta_rule_steps` | 每种事件的标准执行步骤 | AI生成todo list的模板依据 |

**宪法表版本控制**：每次修改留记录，AI每次判断日志中存储 `constitution_version_id`，出问题能完整还原当时执行的是哪套规则。

**AI如何修改宪法表**：AI不能直接修改，只能往 `meta.suggestions` 建议箱里投建议，由管理员审批后执行。

---

## 4. 主数据表（Master Data Layer）

> AI认识人、产品、组织的数据基础。

| 中文名 | 数据库名称 | 目的 |
|--------|-----------|------|
| 员工表 | `meta.employees` | AI认识员工，知道角色，控制权限 |
| 外部联系人表 | `meta.contacts` | AI认识对接人，关联到客户或供应商 |
| 客户表 | `meta.customers` | 销售类事件的核心要素 |
| 供应商表 | `meta.suppliers` | 采购类事件的核心要素 |
| 产品表 | `meta.products` | 报价、订单、样品等事件的核心要素 |

---

## 5. 业务表（Transaction Layer）

> AI建立、跟进、锁定每一个业务事件的运行记录。

| 中文名 | 数据库名称 | 内容 |
|--------|-----------|------|
| 合同主线表 | `business.contracts` | 串联所有相关事件的锚点，轻量设计，细节在事件里 |
| 事件主表 | `business.events` | 每一件事就是一个事件，含 contract_code 外键 |
| 事件步骤表 | `business.event_steps` | todo list实例，由模板生成，含AI判断依据 |
| 附件证据表 | `business.attachments` | 原始文件的元数据和存储路径索引 |
| 合同条款历史 | `business.contract_terms_log` | 精确记录客户/供应商的条款变化历史，支持精确查询 |
| 人员观察记录 | `business.people_observations` | AI对内外部人员的观察，定期聚合为画像 |
| 集中记忆表 | `business.memories` | 所有业务表的语义内容和向量索引，集中存储 |

**合同与事件的关系**：

```
询价阶段          → contract_code = NULL，事件独立存在
询价成交          → AI建议创建合同，用户确认
创建合同          → 生成 contract_code，询价事件回填
后续所有事件      → 都带同一个 contract_code

一份销售合同可以对应多份采购合同（多个供应商）
```

---

## 6. 记忆系统设计

**核心原则：每张业务表都是一个记忆单元。**

```
结构化记忆    → 各业务表的字段（精确查询）
语义记忆      → business.memories（模糊检索）
原始证据      → storage/ 文件夹（原始文件）
```

**三层存储各司其职**：

| 层 | 存什么 | 解决什么问题 |
|----|--------|-------------|
| 结构化数据库 | 字段值、状态、关系 | 精确查询："张先生的付款条件是什么" |
| memories表 | 语义文字 + 向量 | 模糊检索："上次谁提过交期问题" |
| 文件夹 | PDF、图片、邮件原件 | 取回原始证据 |

**memories表设计**：

```sql
business.memories
  source_table  ← 来自哪张表（events/attachments/event_steps...）
  source_id     ← 来自哪条记录
  content       ← 提取的文字内容
  embedding     ← 768维语义向量
```

原表只存结构化字段，语义和向量统一在 memories 里。新增任何业务表，不需要改表结构，只需往 memories 里插数据。

**为什么保留 `contract_terms_log`**：合同条款需要精确查询当前生效版本，不能靠向量模糊检索。memories 是补充，不能替代精确查询。

---

## 7. AI工作流程（8步）

```
第一步：收到任何输入
        ↓
第二步：检索 memories 表
        向量检索相似事件
        结合 meta_rules 的唯一性条件
        有相关事件 → 归入已有事件，补充信息
        没有       → 跟用户确认，新建事件
        ↓
第三步：事件存入 business.events
        原始资料存入 attachments
        文件实体存入 storage/ 文件夹
        同步写入 memories 表
        ↓
第四步：查宪法表
        meta_tables      → 匹配意图，找到目标表
        meta_fields      → 理解字段，核对信息
        meta_rules       → 找到事件类型和规则
        meta_rule_steps  → 找到标准执行步骤
        ↓
第五步：生成 TODO list
        依据 meta_rule_steps 模板生成 event_steps 实例
        标记状态：待处理 / 进行中 / 完成 / 阻塞
        ↓
第六步：循环推进
        缺信息   → 追问用户
        条件满足 → 执行下一步
        用户废弃 → 标记事件作废，保留记录
        ↓
第七步：所有步骤完成，用户最终确认
        ↓
第八步：锁定数据，只读
```

---

## 8. AI权限设计

**核心原则：AI不能直接写SQL，只能调用预定义的存储过程工具。**

```
宪法表（meta schema）    ← AI只读
主数据表（meta schema）  ← AI可INSERT/UPDATE（employees除外）
业务表（business schema）← AI可INSERT/UPDATE（通过存储过程）
文件存储                 ← AI可写入
```

**AI专用数据库用户** `aierp_ai`：只有特定存储过程的执行权限，没有直接 UPDATE/DELETE 权限。就算AI出现幻觉，也无法绕过存储过程直接破坏数据。

**AI成长机制**：

```
🔴 永远不能碰：宪法表、历史锁定数据、员工权限
🟡 学习但需人工确认：建议修改规则、调整流程
🟢 自由积累：observations、terms_log、memories、判断日志
```

AI通过 `meta.suggestions` 建议箱提交修改建议，管理员审批后由存储过程执行，全程留审计记录。

---

## 9. 锁定机制

| 级别 | 触发条件 | 修改方式 |
|------|---------|---------|
| 普通锁定 | 事件进入"已确认"及以上状态 | 需管理员权限，必须记录修改原因 |
| 完全锁定 | 财务数据、合同金额等核心字段 | 不能修改，只能新建更正单 |

**状态机（升序，数字映射）**：

```
意向(1) → 商议中(2) → 已确认(3) → 已下单(4) → 已完成(5)
```

低权限角色无法修改高于其权限阈值的状态数据。状态只能逐级推进，不能跳级，跳级需管理员审批。

---

## 10. 贸易事件体系

**所有贸易活动本质上都是围绕合同展开的**：

```
合同签之前    ← 询价、报价、谈判
合同执行中    ← 采购、备货、跟单、出货
合同收尾      ← 收款、付款、对账、归档
```

**已定义的事件类型**：

| event_type | 中文名 | 触发方式 |
|-----------|--------|---------|
| `inquiry_inbound` | 外部询价 | 邮件/平台窗口自动触发 |
| `inquiry_outbound` | 业务员跟进询价 | 业务员告知AI |
| `sales_contract` | 销售合同跟进 | 询价成交后触发 |
| `purchase_contract` | 采购合同跟进 | 销售合同确认后触发 |
| `shipment` | 出货跟进 | 采购完成后触发 |
| `collection` | 收款跟进 | 出货后触发 |
| `payment` | 付款跟进 | 出货后触发 |

**报价原则**：AI绝对不报价，所有报价必须由业务员完成。原因：无法判断对方身份，可能是竞争对手套价。

---

## 11. 系统核心原则（不变）

| 原则 | 说明 |
|------|------|
| AI只能按规则执行 | 所有行为必须有宪法表中的对应规则 |
| 遇到卡点就报告 | 缺信息、条件不满足，必须上报，不得猜测 |
| 不能自行决策 | 遇到规则未覆盖的情况，停止并等待人工指示 |
| 任何判断必须有出处 | 每个操作都有 rule_id、constitution_version_id 和原始证据支撑 |
| 报价必须由人完成 | AI识别和整理询价，不做报价建议 |

---

## 12. 数据库现状（已建表清单）

**meta schema（宪法 + 主数据）**

```
meta_tables       宪法：表单一览
meta_fields       宪法：字段明细
meta_rules        宪法：事件规则（已填入询价事件规则）
meta_rule_steps   宪法：步骤模板（已填入询价17个步骤）
employees         主数据：员工
contacts          主数据：外部联系人
customers         主数据：客户
suppliers         主数据：供应商
products          主数据：产品
```

**business schema（业务运行数据）**

```
contracts             合同主线
events                事件主表（含contract_code字段）
event_steps           事件步骤
attachments           附件证据
contract_terms_log    合同条款历史（保留，支持精确查询）
people_observations   人员观察
memories              集中记忆（向量）
```

**已建存储过程**

```
meta.sync_constitution()
  作用：自动同步数据库结构到宪法表
  用法：CALL meta.sync_constitution();
  触发时机：新建表、修改字段后运行一次
```

**文件存储结构**

```
aierp/
├── constitution/    SQL文件：存储过程定义
├── readme/          原始设计文档
├── storage/         上传文件实体（按事件编号分文件夹）
└── .git/            Git版本控制（已连接GitHub: Nicjiangray/AIERP）
```

---

## 13. 下一步行动

```
⬜ 执行 create_memories_contracts.sql
     建好 memories 和 contracts 表
     events 表加 contract_code 字段
⬜ 建核心存储过程工具
     create_event()          建事件
     create_contact()        建联系人
     update_step_status()    更新步骤状态
     add_memory()            写入记忆
⬜ 写Python脚本
     连接 Ollama(qwen3.5:35b) + PostgreSQL
     读宪法表构建系统提示词
     接收用户输入，调用AI，执行存储过程
⬜ 测试第一个完整询价事件（inquiry_outbound）
⬜ 验证通过后接入 Claude API 替换本地模型
⬜ 多租户架构改造（有第一批用户后）
⬜ 上云（本地验证完后，阿里云轻量服务器约¥100/月）
```

---

*文档版本：v0.3 · 最后更新：2026年3月*
*原始内容：用户提供 · 整理与补充：Claude*
