"""
意图识别模块：调用 LLM 读取 business.workflows 表，
将用户输入映射为 workflow intent 字符串，无匹配返回 None。
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import ollama as ollama_client

from engine.context_builder import build_table_directory
from llm.client import chat, chat_expert_only
from tools.database import db_conn_llm, get_meta_fields, get_meta_tables

logger = logging.getLogger(__name__)

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "shaw/dmeta-embedding-zh")

# 第二层向量阈值
_EMBED_HIGH = 0.85
_EMBED_LOW = 0.60
_EMBED_TIE_EPS = 1e-6

# is_business_message：业务关键词（命中任一则不过滤）
_BUSINESS_KEYWORDS = (
    "查",
    "找",
    "看",
    "新建",
    "创建",
    "修改",
    "更新",
    "删除",
    "客户",
    "产品",
    "联系人",
    "合同",
    "订单",
    "导出",
    "生成",
    "报告",
)


def is_business_message(user_text: str) -> bool:
    """
    第一层：关键词与模式快速过滤（零 LLM 调用）。
    明显的非业务消息返回 False，不进入意图识别；ambiguous 输入返回 True，交给 detect_intent。
    原则：宁可放行，勿误拦。
    """
    raw = (user_text or "").strip()
    if not raw:
        return False

    for kw in _BUSINESS_KEYWORDS:
        if kw in raw:
            return True

    if len(raw) < 4:
        return False

    lower = raw.lower()
    compact_lo = "".join(
        ch for ch in lower if ch not in " \t\n\r，。！？!?.…~～"
    )
    _greetings = frozenset(
        {"你好", "您好", "hi", "hello", "早上好", "下午好", "晚上好"}
    )
    if compact_lo in _greetings:
        return False

    if any(p in raw for p in ("今天怎么样", "聊聊", "随便问问")):
        return False
    if "天气" in raw:
        return False

    if "谢谢" in raw or "谢了" in raw or "多谢" in raw:
        return False
    if "thanks" in lower or lower.strip() == "thank you":
        return False

    return True


def _parse_llm_json_object(text: str) -> Dict[str, Any]:
    """从 LLM 输出中解析单个 JSON 对象。"""
    raw = (text or "").strip()
    raw = _JSON_FENCE.sub("", raw).strip()
    return json.loads(raw)


def _normalize_conditions_from_llm(raw: Any) -> list[dict[str, str]]:
    """
    将 LLM 返回的 conditions 规范为列表：
    [{"field": "...", "value": "...", "match_type": "exact"|"contains"}]
    兼容旧版：{{ "列名": "值" }} → 全部视为 contains。
    """
    out: list[dict[str, str]] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            field = (item.get("field") or item.get("field_name") or "").strip()
            if not field:
                continue
            val = item.get("value")
            mt = (item.get("match_type") or "contains").strip().lower()
            if mt not in ("exact", "contains"):
                mt = "contains"
            out.append(
                {
                    "field": field,
                    "value": "" if val is None else str(val),
                    "match_type": mt,
                }
            )
    elif isinstance(raw, dict):
        for k, v in raw.items():
            out.append(
                {
                    "field": str(k).strip(),
                    "value": "" if v is None else str(v),
                    "match_type": "contains",
                }
            )
    return out


def extract_query_params(user_text: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """
    用 LLM 从自然语言中抽取主数据查询参数，写入 context：
    - target_table: 英文表名
    - conditions: [{"field","value","match_type"}, ...]，match_type 为 exact 或 contains
    - missing: 仅当需指代消解且无识别条件时填写；泛指「列表/有哪些」类全量查询必须为 []。

    若 context 含 session_id，会在 prompt 开头注入近期对话（turns），便于理解「这个客户」等指代。

    若无法查询（missing 非空），返回 {"follow_up": 追问文案}；
    否则返回 {"follow_up": None}。
    """
    tables = get_meta_tables()
    allowed = {str(r["table_name"]).strip() for r in tables if r.get("table_name")}
    catalog_lines: list[str] = []
    for t in tables:
        tn = str(t.get("table_name") or "").strip()
        if not tn:
            continue
        desc = (t.get("description") or "").strip()
        tcn = (t.get("table_cn") or "").strip()
        try:
            fields = get_meta_fields(tn)
        except Exception as e:
            logger.warning("[intent] get_meta_fields(%r) 失败: %s", tn, e)
            fields = []
        field_parts = []
        for f in fields:
            fn = str(f.get("field_name") or "").strip()
            if not fn:
                continue
            fcn = (f.get("field_cn") or "").strip()
            dt = (f.get("data_type") or "").strip()
            field_parts.append(f"{fn}（{fcn}）" + (f" [{dt}]" if dt else ""))
        fields_str = "；".join(field_parts) if field_parts else "（无字段元数据）"
        catalog_lines.append(
            f"- 表名={tn}，中文名={tcn}，说明={desc}\n  字段清单（仅允许使用下列英文 field_name）：{fields_str}"
        )
    catalog = "\n".join(catalog_lines)

    dialogue_prefix = ""
    sid = context.get("session_id")
    if sid is not None:
        try:
            from engine.turns import format_recent_turns_block

            dialogue_prefix = format_recent_turns_block(
                int(sid),
                limit=10,
                strip_last_user_if_matches=user_text,
            )
        except Exception as e:
            logger.warning("[intent] extract_query_params 近期对话注入失败: %s", e)

    turns_block = dialogue_prefix
    logger.info(
        "extract_query_params: turns_block长度=%d context.session_id=%r",
        len(turns_block),
        context.get("session_id"),
    )

    system_prompt = f"""{dialogue_prefix}你是 ERP 主数据查询参数提取器。根据用户话和下列元数据目录，提取结构化查询参数。

【元数据：每张表及其真实字段（field_name 为英文，必须与字段清单完全一致，禁止自造字段名）】
{catalog}

你必须只输出一个 JSON 对象，不要 Markdown、不要代码围栏、不要任何其它文字。JSON 键如下：
{{
  "target_table": "必须是上表中的英文 table_name 之一",
  "conditions": [
    {{"field": "字段英文名（必须来自该表字段清单）", "value": "条件值字符串", "match_type": "contains"}},
    ...
  ],
  "missing": []
}}

规则：
1. conditions 为数组；每一项含 field、value、match_type。
2. match_type 只能是 \"exact\" 或 \"contains\"：用户要完全相等用 exact；要「包含」「含有」类用 contains。未确定时**默认用 contains**。
3. value 一律用字符串（数字也写成字符串）。
4. 筛选条件里的 field 必须出现在**对应 target_table 的那一行字段清单**中。
5. **泛指全量查询（不要追问）**：若用户是在问「某一类主数据有哪些 / 列表 / 查一下」，例如「看看产品有哪些」「查一下客户列表」「合同有什么」「有哪些供应商」，**没有写筛选条件是正常的**。此时 **missing 必须返回空数组 []**，**conditions 可为 []**；后续会按全量查询（带 LIMIT），**禁止**因「未写筛选条件」而向 missing 里塞追问。
6. **有条件才填 conditions**：用户明确说了地域、名称、编号、状态等可映射到字段的条件时，写入 conditions；例如「查一下上海的客户」应对应 province/city 等合法字段与值。
7. **仅当需要指代消解时才追问**：用户明显指向**某一条**记录、但话里没有任何可落地的识别信息时，才把简短追问短语放进 missing（例如「查一下那个合同」「找那个客户」——未说明编号/名称/对方是谁）。**泛指查询不属于此类。**
8. missing 为有内容的数组时，一般应配合无法落地的 target_table 或空 conditions；泛指列表查询时 missing 必须为 []。
9. **conditions 为空数组 [] 表示无 WHERE 筛选，由后续代码做全量查询（LIMIT），这是正确行为，无需 LLM 干预或追问。**
10. 若上文有「近期对话」，用户若用「这个」「那个」「刚才」「和上面一样」等指代，请结合对话还原 target_table、conditions；仍无法落地时再使用 missing。
"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]

    try:
        raw = chat(messages)
        data = _parse_llm_json_object(raw)
    except Exception as e:
        logger.exception("[intent] extract_query_params 解析失败: %s", e)
        msg = "没能理解您的查询条件，请说明要查哪张表、按哪个字段筛选。"
        context["query_follow_up"] = msg
        return {"follow_up": msg}

    target = (data.get("target_table") or "").strip()
    conditions_raw = data.get("conditions")
    conditions = _normalize_conditions_from_llm(conditions_raw)
    missing = data.get("missing")
    if missing is None:
        missing_list: list[str] = []
    elif isinstance(missing, str):
        missing_list = [missing] if missing.strip() else []
    elif isinstance(missing, list):
        missing_list = [str(x).strip() for x in missing if str(x).strip()]
    else:
        missing_list = []

    context["target_table"] = target
    context["conditions"] = conditions
    context["missing"] = missing_list

    if missing_list:
        follow = (
            "为完成查询还需要您补充：" + "；".join(missing_list) + "。"
        )
        context["query_follow_up"] = follow
        return {"follow_up": follow}

    if not target or target not in allowed:
        follow = "请说明要查询的主数据表（英文表名），或描述再具体一些，例如要按哪个编号/名称筛选。"
        context["query_follow_up"] = follow
        return {"follow_up": follow}

    context.pop("query_follow_up", None)
    return {"follow_up": None}


def _load_workflows() -> list[dict]:
    """从数据库读取所有激活的 workflow（含示例句与向量，供意图识别）。"""
    with db_conn_llm() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT intent, display_name, description, example_queries, query_embeddings,
                       operation_type, target_tables
                FROM business.workflows
                WHERE COALESCE(is_active, 1) = 1
                ORDER BY intent
            """)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def _parse_example_queries_field(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str):
        try:
            j = json.loads(raw)
            if isinstance(j, list):
                return [str(x).strip() for x in j if str(x).strip()]
        except json.JSONDecodeError:
            return []
    return []


def _parse_query_embeddings_field(raw: Any) -> List[List[float]]:
    if raw is None:
        return []
    if isinstance(raw, list):
        out: List[List[float]] = []
        for item in raw:
            if isinstance(item, list):
                out.append([float(x) for x in item])
        return out
    if isinstance(raw, str):
        try:
            j = json.loads(raw)
            if isinstance(j, list):
                return [[float(x) for x in row] for row in j if isinstance(row, list)]
        except json.JSONDecodeError:
            return []
    return []


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """计算两个向量的余弦相似度。"""
    va, vb = np.array(a, dtype=np.float64), np.array(b, dtype=np.float64)
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def _get_embedding(text: str) -> Optional[List[float]]:
    """调用本地 Ollama 获取文本向量；失败返回 None。

    使用官方 ``ollama`` 包：优先 ``embed``（/api/embed），失败再试已弃用的 ``embeddings``（/api/embeddings）。
    需已 ``ollama pull <模型名>``（默认见环境变量 ``OLLAMA_EMBED_MODEL``）。
    """
    try:
        r = ollama_client.embed(model=OLLAMA_EMBED_MODEL, input=text)
        if r.embeddings and len(r.embeddings) > 0:
            return [float(x) for x in r.embeddings[0]]
    except Exception as e:
        logger.debug("[intent] ollama.embed 失败: %s", e)
    try:
        r = ollama_client.embeddings(model=OLLAMA_EMBED_MODEL, prompt=text)
        if r.embedding:
            return [float(x) for x in r.embedding]
    except Exception as e:
        logger.warning("[intent] embedding 失败（请确认已拉取模型 %s）：%s", OLLAMA_EMBED_MODEL, e)
    return None


def _load_business_meta_for_cn_match() -> List[Tuple[str, str]]:
    """business 基表在 meta 中的 table_name / table_cn，供中文子串匹配。"""
    with db_conn_llm() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT m.table_name, COALESCE(m.table_cn, '')
                FROM meta.meta_tables m
                WHERE EXISTS (
                    SELECT 1
                    FROM information_schema.tables t
                    WHERE t.table_schema = 'business'
                      AND t.table_name = m.table_name
                      AND t.table_type = 'BASE TABLE'
                )
                """
            )
            return [(str(r[0]), str(r[1])) for r in cur.fetchall()]


def _table_cn_match_variants(table_cn: str) -> List[str]:
    """用于子串匹配：全名 + 去掉末尾「表」的简称（如 客户表 → 客户）。"""
    tcn = (table_cn or "").strip()
    if not tcn:
        return []
    out: List[str] = [tcn]
    if tcn.endswith("表") and len(tcn) > 1:
        short = tcn[:-1].strip()
        if len(short) >= 1:
            out.append(short)
    return out


def _infer_target_table_from_meta_cn(user_text: str) -> Optional[str]:
    """
    零 LLM：用户话中若包含某业务表的 table_cn（或简称）子串，取长匹配；
    否则若包含英文 table_name 子串，取长匹配。
    """
    ut = (user_text or "").strip()
    if not ut:
        return None
    rows = _load_business_meta_for_cn_match()
    best: Optional[str] = None
    best_len = 0
    for tn, tcn in rows:
        tcn = (tcn or "").strip()
        tn = (tn or "").strip()
        if not tn:
            continue
        for variant in _table_cn_match_variants(tcn):
            if len(variant) < 1:
                continue
            if variant in ut:
                if len(variant) > best_len:
                    best_len = len(variant)
                    best = tn
    if best:
        return best
    ut_lower = ut.lower()
    best_len = 0
    for tn, _tcn in rows:
        tn = (tn or "").strip()
        if len(tn) < 3:
            continue
        if tn.lower() in ut_lower:
            if len(tn) > best_len:
                best_len = len(tn)
                best = tn
    return best


def infer_target_table_from_user_text(user_text: str) -> Optional[str]:
    """供路由层与 collecting frame 的 target_table 比对（零 LLM，与侧车推断同源）。"""
    return _infer_target_table_from_meta_cn(user_text)


def _apply_inferred_target_to_context(
    user_text: str,
    context: Dict[str, Any],
    intent: Optional[str],
    workflows: List[dict],
) -> None:
    """将推断的 target_table 写入 context；受 workflow.target_tables 非空列表约束。"""
    if not intent:
        return
    inf = _infer_target_table_from_meta_cn(user_text)
    if not inf:
        return
    wf_map = {str(w.get("intent")): w for w in workflows if w.get("intent")}
    wrow = wf_map.get(intent)
    if wrow:
        tarr = wrow.get("target_tables")
        if isinstance(tarr, list) and len(tarr) > 0:
            allowed = {str(x).strip() for x in tarr if x}
            if allowed and inf not in allowed:
                logger.debug(
                    "[intent] 推断表 %r 不在 workflow %s 的 target_tables 中，跳过",
                    inf,
                    intent,
                )
                return
    context["target_table"] = inf


def _layer1_substring_match(user_text: str, workflows: List[dict]) -> Optional[str]:
    """第一层：示例句与用户话双向子串匹配（example in user_text 或 user_text in example）。"""
    ut = (user_text or "").strip()
    if not ut:
        return None
    for w in workflows:
        intent = w.get("intent")
        if not intent:
            continue
        for ex in _parse_example_queries_field(w.get("example_queries")):
            ex = (ex or "").strip()
            if not ex:
                continue
            if ex in ut or ut in ex:
                logger.info("[intent] 第一层命中: %s", intent)
                return str(intent)
    return None


def _layer2_embedding_scores(
    user_text: str, workflows: List[dict], user_vec: List[float]
) -> List[Tuple[str, float]]:
    """每个 intent 取与所有示例向量的最大余弦相似度。"""
    scores: List[Tuple[str, float]] = []
    for w in workflows:
        intent = w.get("intent")
        if not intent:
            continue
        embs = _parse_query_embeddings_field(w.get("query_embeddings"))
        if not embs:
            scores.append((str(intent), 0.0))
            continue
        best = 0.0
        for vec in embs:
            if len(vec) != len(user_vec):
                continue
            best = max(best, _cosine_similarity(user_vec, vec))
        scores.append((str(intent), best))
    scores.sort(key=lambda x: -x[1])
    return scores


def _intent_tokens(s: str) -> list[str]:
    return [t for t in s.strip().lower().split("_") if t]


def _is_token_subsequence(a: list[str], b: list[str]) -> bool:
    """判断 a 是否为 b 的子序列（按顺序出现，不必相邻）。"""
    if not a:
        return False
    i = 0
    for x in b:
        if i < len(a) and a[i] == x:
            i += 1
    return i == len(a)


def _resolve_intent_fuzzy(llm_result: str, known: set[str]) -> Optional[str]:
    """
    LLM 输出与库中 intent 不完全一致时的保守兜底（须在「精确匹配」已失败之后调用）。

    顺序：显式别名 → 大小写已在外层处理 → 唯一前缀（截断）→ 唯一「反向前缀」→
    唯一 token 子序列（如 query_data 对应 query_master_data 的中间省略）。

    若多种解释并存则返回 None，避免把 create 等短串误绑到错误流程。
    """
    raw = (llm_result or "").strip()
    if not raw:
        return None
    low = raw.lower()

    # 显式别名：常见截断/误拼
    aliases = {
        "query_data": "query_master_data",
    }
    if low in aliases:
        cand = aliases[low]
        if cand in known:
            logger.info("[intent] 别名兜底: %r → %r", llm_result, cand)
            return cand

    # 合法 intent 以 LLM 输出为前缀（模型截断了末尾）
    prefix_hits = [v for v in known if v.lower().startswith(low)]
    if len(prefix_hits) == 1:
        logger.info("[intent] 唯一前缀兜底: %r → %r", llm_result, prefix_hits[0])
        return prefix_hits[0]

    # LLM 输出以某个合法 intent 为前缀（多打了后缀等，少见）
    rev_hits = [v for v in known if low.startswith(v.lower()) and len(low) > len(v)]
    if len(rev_hits) == 1:
        logger.info("[intent] 唯一反向前缀兜底: %r → %r", llm_result, rev_hits[0])
        return rev_hits[0]

    a = _intent_tokens(raw)
    if not a:
        return None
    sub_hits = [v for v in known if _is_token_subsequence(a, _intent_tokens(v))]
    if len(sub_hits) == 1:
        logger.info("[intent] 唯一 token 子序列兜底: %r → %r", llm_result, sub_hits[0])
        return sub_hits[0]

    return None


def _detect_intent_dynamic(
    user_text: str,
    candidate_workflows: List[dict],
    use_expert: bool,
    context: Dict[str, Any],
) -> Optional[str]:
    """
    第三层：表级目录 + 候选 workflow，输出 JSON intent / target_table / reason。
    写入 context：target_table、intent_reason（解析成功时）。
    """
    if not candidate_workflows:
        return None
    known = {str(w["intent"]) for w in candidate_workflows if w.get("intent")}
    if not known:
        return None

    directory = build_table_directory(candidate_workflows)
    meta_tables = get_meta_tables()
    meta_names = {
        str(r["table_name"]).strip()
        for r in meta_tables
        if r.get("table_name")
    }

    intents_str = ", ".join(sorted(known))

    dialogue_prefix = ""
    sid = context.get("session_id")
    if sid is not None:
        try:
            from engine.turns import format_recent_turns_block

            dialogue_prefix = format_recent_turns_block(
                int(sid),
                limit=6,
                strip_last_user_if_matches=user_text,
                heading="近期对话（供指代消解）",
            )
        except Exception as e:
            logger.warning("[intent] _detect_intent_dynamic 近期对话注入失败: %s", e)

    system_prompt = f"""{dialogue_prefix}你是 ERP 意图识别助手。根据用户输入（可结合下方近期对话理解指代），从「本次候选 intent」中选出一个最匹配的流程，并尽可能给出 target_table。

{directory}

## 本次候选 intent（intent 字段必须且仅能是下列之一）
{intents_str}

你必须只输出一个 JSON 对象，不要 Markdown、不要代码围栏、不要其它文字。键如下：
{{"intent": "<英文 intent 字符串或 null>", "target_table": "<英文表名或 null>", "reason": "<一句简短理由>"}}

规则：
1. 若用户闲聊、问能力、与业务无关、或无法对应任一候选 intent，intent 填 null，target_table 填 null。
2. intent 非 null 时，必须是「本次候选」中的某一个，与系统中注册的 intent 英文名完全一致。
3. target_table 填业务主表英文名（如 customers、products、contacts），须与上文「系统中的业务表」中的英文表名一致；不确定时填 null。
4. query_master_data：根据用户要查的数据类型给出 target_table。
5. create_simple_entity / update_simple_entity / delete_simple_entity：应给出要操作的客户/产品/联系人对应表。
6. 若用户是在**核对、复查、验证**刚完成的操作或某条数据的**当前状态**（例如「你检查下」「帮我看看对不对」「确认下结果」「再看一眼客户信息」），
   且涉及主数据查看而非修改，应归为 **query_master_data**，并给出对应 target_table。
7. 「看看」「检查」「确认」若明显是闲聊、与业务数据无关，则 intent 仍为 null。
8. 若用户只说「就是那个」「上面提到的」「刚才那个」「再查一下」等**指代性**表达，请结合「近期对话」判断：若仍在问主数据或核对结果，对应 **query_master_data**（并尽量给出 target_table）；若无法与业务表对应再填 null。
"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]
    try:
        if use_expert:
            raw = chat_expert_only(messages)
        else:
            raw = chat(messages)
        data = _parse_llm_json_object(raw)
    except Exception as e:
        logger.exception("[intent] 动态意图识别解析失败: %s", e)
        return None

    raw_intent = data.get("intent")
    if raw_intent is None:
        return None
    if isinstance(raw_intent, str) and raw_intent.strip().lower() in ("null", "none", ""):
        return None
    if not isinstance(raw_intent, str):
        raw_intent = str(raw_intent)
    raw_intent = raw_intent.strip()
    if not raw_intent:
        return None

    resolved: Optional[str] = None
    if raw_intent in known:
        resolved = raw_intent
    else:
        resolved = _resolve_intent_fuzzy(raw_intent, known)
    if not resolved:
        logger.warning("[intent] 动态识别未知 intent: %r", raw_intent)
        return None

    tt_raw = data.get("target_table")
    tt = ""
    if tt_raw is not None and not (
        isinstance(tt_raw, str) and tt_raw.strip().lower() in ("null", "none")
    ):
        tt = str(tt_raw).strip()

    if tt:
        if tt not in meta_names:
            lower = tt.lower()
            hits = [n for n in meta_names if n.lower() == lower]
            tt = hits[0] if len(hits) == 1 else ""

    wf_map = {str(w["intent"]): w for w in candidate_workflows if w.get("intent")}
    wrow = wf_map.get(resolved)
    if wrow and tt:
        tarr = wrow.get("target_tables")
        if isinstance(tarr, list) and len(tarr) > 0:
            allowed_tt = {str(x).strip() for x in tarr if x}
            if allowed_tt and tt not in allowed_tt:
                logger.info(
                    "[intent] target_table %r 不在 workflow %s 的 target_tables 中，忽略",
                    tt,
                    resolved,
                )
                tt = ""

    reason = data.get("reason")
    if isinstance(reason, str) and reason.strip():
        context["intent_reason"] = reason.strip()
    if tt:
        context["target_table"] = tt

    if not context.get("target_table"):
        _apply_inferred_target_to_context(
            user_text, context, resolved, candidate_workflows
        )

    logger.info(
        "[intent] 第三层动态识别: intent=%r target_table=%r",
        resolved,
        context.get("target_table"),
    )
    return resolved


def _intent_log_preview(user_text: str, max_len: int = 30) -> str:
    t = (user_text or "").strip().replace("\n", " ")
    if len(t) <= max_len:
        return t
    return t[:max_len] + "…"


def detect_intent(
    user_text: str,
    use_expert: bool = False,
    context: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """
    三层意图识别：示例句子串匹配 → 向量相似度 → LLM 兜底（仅候选）。

    use_expert=True 时第三层仅调用云端专家模型。
    context 可选：命中 intent 时尽量写入 target_table（第一层/第二层用 meta.table_cn 子串推断；
    第三层为 LLM JSON + 相同推断兜底）、第三层另写入 intent_reason。
    若 context 含 session_id，第三层会在 prompt 中注入近期对话摘要，便于指代表达消解。
    """
    ctx: Dict[str, Any] = context if context is not None else {}
    workflows = _load_workflows()
    if not workflows:
        logger.info("[intent.最终] intent=%r layer=%s", None, "none")
        return None

    preview = _intent_log_preview(user_text)
    hit = _layer1_substring_match(user_text, workflows)
    logger.info("[intent.层1] text=%r result=%r", preview, hit)
    if hit:
        _apply_inferred_target_to_context(user_text, ctx, hit, workflows)
        logger.info("[intent.最终] intent=%r layer=%s", hit, 1)
        return hit

    user_vec = _get_embedding(user_text)
    if user_vec is None:
        logger.warning(
            "[intent] embedding失败，第二层跳过，第三层使用全部intent"
        )
        logger.info("[intent.层2] top_sim=%s candidates=%s", "embedding_failed", "[]")
        r3 = _detect_intent_dynamic(user_text, workflows, use_expert, ctx)
        logger.info("[intent.层3] llm_result=%r", r3)
        logger.info("[intent.最终] intent=%r layer=%s", r3, 3)
        return r3

    scores = _layer2_embedding_scores(user_text, workflows, user_vec)
    if not scores:
        logger.info("[intent.层2] top_sim=%s candidates=%s", "none", "[]")
        logger.info("[intent.层3] llm_result=%r", None)
        logger.info("[intent.最终] intent=%r layer=%s", None, "none")
        return None

    best_intent, best_s = scores[0]
    cand_preview = [(i, round(s, 3)) for i, s in scores[:8]]

    if best_s > _EMBED_HIGH:
        tied = [i for i, s in scores if s >= best_s - _EMBED_TIE_EPS]
        logger.info(
            "[intent.层2] top_sim=%.3f candidates=%s",
            best_s,
            cand_preview,
        )
        if len(tied) == 1:
            logger.info(
                "[intent] 第二层命中: %s 相似度=%.3f",
                best_intent,
                best_s,
            )
            _apply_inferred_target_to_context(
                user_text, ctx, best_intent, workflows
            )
            logger.info("[intent.层3] llm_result=%r", None)
            logger.info("[intent.最终] intent=%r layer=%s", best_intent, 2)
            return best_intent
        cand = [w for w in workflows if w.get("intent") in tied]
        logger.info(
            "[intent] 第二层相似度>%.2f 多路并列，进入第三层: %s",
            _EMBED_HIGH,
            tied,
        )
        r3 = _detect_intent_dynamic(user_text, cand, use_expert, ctx)
        logger.info("[intent.层3] llm_result=%r", r3)
        logger.info("[intent.最终] intent=%r layer=%s", r3, 3)
        return r3

    if best_s < _EMBED_LOW:
        logger.info(
            "[intent.层2] top_sim=%.3f candidates=%s",
            best_s,
            cand_preview,
        )
        logger.info(
            "[intent] 第二层最高相似度 %.3f < %.2f，进入第三层（全量候选）",
            best_s,
            _EMBED_LOW,
        )
        r3 = _detect_intent_dynamic(user_text, workflows, use_expert, ctx)
        logger.info("[intent.层3] llm_result=%r", r3)
        logger.info("[intent.最终] intent=%r layer=%s", r3, 3)
        return r3

    # 0.60 ~ 0.85：带候选进第三层
    cand_intents = [i for i, s in scores if s >= _EMBED_LOW]
    cand = [w for w in workflows if w.get("intent") in cand_intents]
    logger.info(
        "[intent.层2] top_sim=%.3f candidates=%s",
        best_s,
        cand_preview,
    )
    logger.info(
        "[intent] 第二层未达 %.2f，进入第三层（候选相似度 [%.2f, %.2f]）: %s",
        _EMBED_HIGH,
        _EMBED_LOW,
        _EMBED_HIGH,
        cand_intents,
    )
    r3 = _detect_intent_dynamic(user_text, cand, use_expert, ctx)
    logger.info("[intent.层3] llm_result=%r", r3)
    logger.info("[intent.最终] intent=%r layer=%s", r3, 3)
    return r3


def explain_capabilities(user_text: str) -> str:
    """
    当意图识别返回 None 时调用。
    让 LLM 根据当前所有激活 workflow，生成友好的能力说明 + 引导回复。
    """
    workflows = _load_workflows()

    workflow_list_str = "\n".join(
        f"- {w['display_name']}：{w.get('description', '')}"
        for w in workflows
    )

    system_prompt = f"""你是企业 ERP 助手。用户提出了一个你暂时无法处理的请求。
请用友好、简洁的中文回复用户，说明你目前能处理的业务范围，并询问用户是否需要其中某项帮助。

你目前支持的业务流程如下：
{workflow_list_str}

要求：
1. 先简短说明无法处理用户的具体请求
2. 列出你支持的所有业务（直接用上方列表，不要省略）
3. 结尾询问用户是否需要处理其中某项
4. 语气自然，不要过于正式
"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]

    try:
        result = chat(messages)
        return result or "抱歉，我暂时无法处理您的请求。如需帮助，请告诉我您想处理哪项业务。"
    except Exception as e:
        logger.exception("[intent] explain_capabilities 失败: %s", e)
        return "抱歉，我暂时无法处理您的请求。如需帮助，请告诉我您想处理哪项业务。"
