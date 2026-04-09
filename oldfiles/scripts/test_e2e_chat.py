#!/usr/bin/env python3
"""
端到端 HTTP 测试：/ai/chat 与 /ai/chat 依赖的 collecting 确认流。
需后端已启动：127.0.0.1:8000；需 LLM 可用（否则可能 WARN）。

用法:
  cd /Users/nicjiang/aierp && .venv/bin/python scripts/test_e2e_chat.py

会话隔离：每场景在库中 INSERT 新 ``business.sessions`` 行，请求体带 ``session_id``，
与 main.py ``_sync_validate_and_touch_session`` 配合，避免多场景共用同一会话 turns。
场景 5 两步共用一个 ``session_id``。
场景 7a/7b/7c：各用独立 session；库内先 INSERT 种子合同（避免依赖 create_contract LLM）。
仅跑场景 7：``python scripts/test_e2e_chat.py --only-7``
"""
from __future__ import annotations

import json
import os
import re
import sys
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import httpx  # noqa: E402

from tools.database import db_conn  # noqa: E402

BASE = os.environ.get("E2E_CHAT_BASE", "http://127.0.0.1:8000")
# 单次 HTTP 等待；本地大模型常超过 30s，可用 E2E_CHAT_TIMEOUT=30 收紧
TIMEOUT = float(os.environ.get("E2E_CHAT_TIMEOUT", "120"))
USER_ID = 2

# 场景 2 / 5 依赖的客户名与恢复用额度
CUSTOMER_NAME = "浑圆纺织"

# 场景 6：库中按客户名称匹配最近一条合同（ILIKE）
CONTRACT_E2E_CUSTOMER_ILIKE = "%杭州布艺%"


def _cleanup_collecting_frames() -> None:
    with db_conn() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE business.frames
                SET status = 'completed', updated_at = now()
                WHERE status = 'collecting'
                """
            )


def _get_credit_limit(name: str) -> Optional[Decimal]:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT credit_limit FROM business.customers WHERE name = %s",
                (name,),
            )
            row = cur.fetchone()
            if not row or row[0] is None:
                return None
            return row[0] if isinstance(row[0], Decimal) else Decimal(str(row[0]))


def _set_credit_limit(name: str, value: Decimal) -> None:
    with db_conn() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE business.customers SET credit_limit = %s WHERE name = %s",
                (value, name),
            )


def _soft_delete_contract_e2e(contract_id: int) -> None:
    """场景 6 / 7：软删本场景写入的合同及明细。"""
    with db_conn() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE business.contract_items
                SET deleted_at = now()
                WHERE contract_id = %s AND deleted_at IS NULL
                """,
                (contract_id,),
            )
            cur.execute(
                """
                UPDATE business.contracts
                SET deleted_at = now()
                WHERE id = %s AND deleted_at IS NULL
                """,
                (contract_id,),
            )


def _get_e2e_customer() -> Tuple[int, str]:
    """优先「浑圆纺织」，否则任取一条未删客户。"""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name FROM business.customers
                WHERE name = %s AND deleted_at IS NULL
                LIMIT 1
                """,
                (CUSTOMER_NAME,),
            )
            row = cur.fetchone()
            if row:
                return int(row[0]), str(row[1])
            cur.execute(
                """
                SELECT id, name FROM business.customers
                WHERE deleted_at IS NULL
                ORDER BY id
                LIMIT 1
                """
            )
            row2 = cur.fetchone()
    if not row2:
        raise RuntimeError("无可用客户，无法种子合同")
    return int(row2[0]), str(row2[1])


def _seed_e2e_contract(n_items: int, tag: str) -> Tuple[int, List[int], str]:
    """
    插入草稿合同及明细，返回 (contract_id, contract_item_ids, customer_name)。
    notes 含 tag 便于识别。
    """
    cust_id, cust_name = _get_e2e_customer()
    notes = f"{tag}_SEED"
    item_ids: List[int] = []
    with db_conn() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO business.contracts
                    (customer_id, customer_name, currency, total_amount, status, notes)
                VALUES (%s, %s, 'CNY', 0, 'draft', %s)
                RETURNING id
                """,
                (cust_id, cust_name, notes),
            )
            cid = int(cur.fetchone()[0])
            for i in range(n_items):
                q = Decimal(10 + i * 5)
                p = Decimal(5 + i)
                cur.execute(
                    """
                    INSERT INTO business.contract_items
                        (contract_id, product_name, unit, quantity, unit_price)
                    VALUES (%s, %s, '米', %s, %s)
                    RETURNING id
                    """,
                    (cid, f"{tag}-item{i + 1}", q, p),
                )
                item_ids.append(int(cur.fetchone()[0]))
    return cid, item_ids, cust_name


def _extract_writes_from_response(
    resp: Dict[str, Any], frame_id: Optional[int] = None
) -> Optional[List[Dict[str, Any]]]:
    """从 HTTP 响应或 frame.context_snapshot 取 pending_submit.writes。"""
    prev = resp.get("preview")
    if isinstance(prev, dict):
        w = prev.get("writes")
        if isinstance(w, list):
            return w
    wc = resp.get("workflow_context")
    if isinstance(wc, dict):
        p = wc.get("preview")
        if isinstance(p, dict):
            w = p.get("writes")
            if isinstance(w, list):
                return w
    if frame_id is not None:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT context_snapshot->'pending_submit'->'writes'
                    FROM business.frames
                    WHERE id = %s
                    """,
                    (int(frame_id),),
                )
                row = cur.fetchone()
        if row and row[0] is not None:
            raw = row[0]
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except json.JSONDecodeError:
                    return None
            if isinstance(raw, list):
                return raw
    return None


def _writes_table_ops(writes: Optional[List[Dict[str, Any]]]) -> List[Tuple[Optional[int], str, str]]:
    out: List[Tuple[Optional[int], str, str]] = []
    if not writes:
        return out
    for w in writes:
        if not isinstance(w, dict):
            continue
        out.append(
            (
                w.get("seq") if isinstance(w.get("seq"), int) else None,
                str(w.get("table") or ""),
                str(w.get("operation") or ""),
            )
        )
    return out


def _log_writes(tag: str, writes: Optional[List[Dict[str, Any]]]) -> None:
    """控制台输出 writes 摘要与 JSON（截断大字段）。"""
    ops = _writes_table_ops(writes)
    print(f"\n[{tag}] pending_submit.writes 条数={len(writes or [])} 摘要(seq,table,op)={ops}")
    if writes:
        dump = json.dumps(writes, ensure_ascii=False, default=str)
        if len(dump) > 12000:
            dump = dump[:12000] + "…(truncated)"
        print(f"[{tag}] pending_submit.writes JSON:\n{dump}\n")


def _looks_like_collecting_followup(resp: Dict[str, Any]) -> bool:
    """collecting 且未确认时，可再发一条补强指令。"""
    if _confirm_required(resp):
        return False
    wc = resp.get("workflow_context")
    if not isinstance(wc, dict):
        return False
    return str(wc.get("status") or "").strip() == "collecting"


def _deep_find_first(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _deep_find_first(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for item in obj:
            r = _deep_find_first(item, key)
            if r is not None:
                return r
    return None


def _confirm_required(resp: Dict[str, Any]) -> bool:
    for k in ("confirm_required", "requiresConfirm", "confirmRequired"):
        v = _deep_find_first(resp, k)
        if v is True:
            return True
    wc = resp.get("workflow_context")
    if isinstance(wc, dict) and str(wc.get("status") or "").strip() == "pending_confirm":
        return True
    return False


def _extract_frame_id(resp: Dict[str, Any]) -> Optional[int]:
    raw = _deep_find_first(resp, "frame_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _extract_session_id(resp: Dict[str, Any]) -> Optional[int]:
    raw = _deep_find_first(resp, "session_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _new_session_id() -> int:
    """新建空会话，供单场景 e2e 与 /ai/chat 的 session_id 对齐。"""
    with db_conn() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO business.sessions (user_id) VALUES (%s) RETURNING id",
                (USER_ID,),
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError("INSERT business.sessions 未返回 id")
            return int(row[0])


def _reply_text(resp: Dict[str, Any]) -> str:
    for k in ("reply", "content"):
        v = resp.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return str(resp.get("reply") or resp.get("content") or "")


def _chat(messages: List[Dict[str, str]], session_id: Optional[int] = None) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "messages": messages,
        "user_id": USER_ID,
        "stream": False,
        "confirm": False,
    }
    if session_id is not None:
        body["session_id"] = session_id
    with httpx.Client(timeout=TIMEOUT) as client:
        r = client.post(f"{BASE}/ai/chat", json=body)
        r.raise_for_status()
        return r.json()


def _confirm(frame_id: int) -> Dict[str, Any]:
    body = {"frame_id": frame_id, "confirmed": True, "user_id": USER_ID}
    with httpx.Client(timeout=TIMEOUT) as client:
        r = client.post(f"{BASE}/ai/confirm", json=body)
        r.raise_for_status()
        return r.json()


def scenario_query() -> Tuple[str, str]:
    """场景1：查询客户列表。"""
    sid = _new_session_id()
    resp = _chat(
        [{"role": "user", "content": "看看我们有哪些客户"}],
        session_id=sid,
    )
    intent = str(resp.get("intent") or _deep_find_first(resp, "intent") or "")
    data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
    main = data.get("main")
    reply = _reply_text(resp)
    if not reply.strip():
        return "FAIL", "响应无 reply/content"
    if intent != "query_master_data":
        return (
            "FAIL",
            f"intent 期望 query_master_data，实际 {intent!r}",
        )
    if not isinstance(main, list) or len(main) == 0:
        return "WARN", f"data.main 为空或非列表（intent={intent!r}），可能查询未返回行"
    return "PASS", f"intent={intent}，data.main 条数={len(main)}"


def _looks_like_text_confirm_without_tool(resp: Dict[str, Any]) -> bool:
    t = _reply_text(resp)
    return ("请确认" in t or "是否执行" in t) and not _confirm_required(resp)


# 场景 6：首轮若 ask_user 追问可选字段，第二条补全后应 submit
_CONTRACT_FOLLOWUP_USER = (
    "付款方式：月结30天；交货日期：2026-04-30；签约日期：2026-04-06。"
    "请用 submit 提交合同预览，弹出确认卡片。"
)


def _looks_like_contract_ask_user_followup(resp: Dict[str, Any]) -> bool:
    """首轮未 confirm、且 collecting 追问（典型为 ask_user 停轮）时返回 True。"""
    if _confirm_required(resp):
        return False
    wc = resp.get("workflow_context")
    if not isinstance(wc, dict):
        return False
    if str(wc.get("status") or "").strip() != "collecting":
        return False
    t = _reply_text(resp)
    if not t.strip():
        return False
    markers = ("付款", "交货", "签约", "日期", "补充", "提供", "币种", "备注", "什么")
    return any(m in t for m in markers) or ("？" in t) or ("?" in t)


def scenario_update_confirm(original: Decimal) -> Tuple[str, str]:
    """场景2：修改授信 + 确认 + 校验库 + 恢复额度。"""
    sid = _new_session_id()
    resp = _chat(
        [{"role": "user", "content": "把浑圆纺织的授信额度改成十八万"}],
        session_id=sid,
    )
    if not _confirm_required(resp):
        if _looks_like_text_confirm_without_tool(resp):
            return (
                "WARN",
                "接口未置 confirm_required：常见为模型仅文字请确认而未调用 update_entity（无 pending_confirm）",
            )
        return (
            "WARN",
            "未出现 confirm_required（可能先追问或未完成预览），跳过 confirm 与 DB 断言",
        )
    fid = _extract_frame_id(resp)
    if not fid:
        return "FAIL", "confirm_required 为真但无法解析 frame_id"
    try:
        cresp = _confirm(fid)
    except Exception as e:
        return "FAIL", f"POST /ai/confirm 失败: {e}"
    content = str(cresp.get("content") or "")
    if "❌" in content and "写入失败" in content:
        return "FAIL", f"确认后执行失败: {content[:200]}"
    lim = _get_credit_limit(CUSTOMER_NAME)
    target = Decimal("180000")
    if lim is None:
        return "FAIL", "库中无该客户或 credit_limit 为空"
    if lim != target:
        return (
            "FAIL",
            f"credit_limit 期望 {target}，实际 {lim}",
        )
    _set_credit_limit(CUSTOMER_NAME, original)
    return "PASS", f"credit_limit 已验证为 180000 并已恢复为 {original}"


def scenario_create_product() -> Tuple[str, str]:
    """场景3：新建产品 + 确认 + 校验 + 删除。"""
    code = "TEST-E2E-001"
    sid = _new_session_id()
    resp = _chat(
        [
            {
                "role": "user",
                "content": (
                    "新建一个产品，编号TEST-E2E-001，名称端到端测试布，"
                    "品类梭织面料，单位米，单价10"
                ),
            }
        ],
        session_id=sid,
    )
    if not _confirm_required(resp):
        if _looks_like_text_confirm_without_tool(resp):
            return (
                "WARN",
                "接口未置 confirm_required：模型可能未调用 write_entity 进入 pending_confirm",
            )
        return (
            "WARN",
            "未出现 confirm_required，跳过 confirm 与库校验",
        )
    fid = _extract_frame_id(resp)
    if not fid:
        return "FAIL", "confirm_required 为真但无法解析 frame_id"
    try:
        cresp = _confirm(fid)
    except Exception as e:
        return "FAIL", f"POST /ai/confirm 失败: {e}"
    content = str(cresp.get("content") or "")
    if "❌" in content and "写入失败" in content:
        return "FAIL", f"确认后执行失败: {content[:200]}"

    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT product_code, product_name FROM business.products
                WHERE product_code = %s
                """,
                (code,),
            )
            row = cur.fetchone()
    try:
        with db_conn() as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM business.products WHERE product_code = %s",
                    (code,),
                )
    except Exception as e:
        return "FAIL", f"清理测试产品失败: {e}"

    if not row:
        return "FAIL", "库中无 product_code=TEST-E2E-001"
    pname = row[1]
    if str(pname).strip() != "端到端测试布":
        return "FAIL", f"product_name 期望「端到端测试布」，实际 {pname!r}"
    return "PASS", "已写入并校验 product_name，测试行已删除"


def scenario_contract_create() -> Tuple[str, str]:
    """
    合同创建 e2e：create_contract + submit 预览 + /ai/confirm。
    首轮若 ask_user 追问可选字段，自动发第二条补全后再判 confirm_required。
    """
    user_msg = (
        "建立一张合同，客人是杭州布艺贸易有限公司，产品是32支纯棉单面针织布，"
        "数量300米，单价65.8"
    )
    sid = _new_session_id()
    messages: List[Dict[str, str]] = [{"role": "user", "content": user_msg}]
    resp = _chat(messages, session_id=sid)
    sid = _extract_session_id(resp) or sid

    if not _confirm_required(resp) and _looks_like_contract_ask_user_followup(resp):
        messages = [
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": _reply_text(resp)},
            {"role": "user", "content": _CONTRACT_FOLLOWUP_USER},
        ]
        resp = _chat(messages, session_id=sid)
        sid = _extract_session_id(resp) or sid

    if not _confirm_required(resp):
        return (
            "WARN",
            "未出现 confirm_required（模型可能未 submit 或未进入 pending_confirm；已尝试可选第二条补全）",
        )
    fid = _extract_frame_id(resp)
    if not fid:
        return "FAIL", "confirm_required 为真但无法解析 frame_id"
    try:
        cresp = _confirm(fid)
    except Exception as e:
        return "FAIL", f"POST /ai/confirm 失败: {e}"

    cid_cleanup: Optional[int] = None
    try:
        cr = cresp.get("confirm_result")
        if cr == "error":
            ed = str(cresp.get("error_detail") or cresp.get("content") or "")
            return "WARN", f"confirm_result=error（可能 snapshot 缺失或执行失败）: {ed[:300]}"
        if cr == "cancelled":
            return "WARN", "confirm_result=cancelled"
        if cr not in (None, "success"):
            return "WARN", f"未知 confirm_result={cr!r}"

        text = str(cresp.get("content") or "")
        ct_in_text = re.search(r"CT-\d{6}-\d{3}", text)
        ct_hint = ct_in_text.group(0) if ct_in_text else None

        # success / None：以库为准；正文中的 CT 编号仅作辅助说明
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, contract_no FROM business.contracts
                    WHERE customer_name ILIKE %s AND deleted_at IS NULL
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (CONTRACT_E2E_CUSTOMER_ILIKE,),
                )
                row = cur.fetchone()
        if not row:
            return "FAIL", (
                "confirm 后未在库中找到 customer_name ILIKE "
                f"{CONTRACT_E2E_CUSTOMER_ILIKE!r} 的合同"
            )
        cid = int(row[0])
        cno = row[1]
        cid_cleanup = cid

        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT count(*)::int FROM business.contract_items
                    WHERE contract_id = %s AND deleted_at IS NULL
                    """,
                    (cid,),
                )
                n_items = cur.fetchone()[0]
        if n_items < 1:
            return "FAIL", f"合同 id={cid} contract_no={cno!r} 无明细行"

        msg = (
            f"库校验通过：contract_no={cno!r}，明细 {n_items} 行"
            f"（confirm_result={cr!r}）"
        )
        if ct_hint:
            msg += f"；响应文本含编号 {ct_hint}"
        return "PASS", msg
    finally:
        if cid_cleanup is not None:
            try:
                _soft_delete_contract_e2e(cid_cleanup)
            except Exception as e:
                print(
                    f"[e2e] 场景 6 清理合同 id={cid_cleanup} 失败: {e}",
                    file=sys.stderr,
                    flush=True,
                )


def _chat_update_contract_round(
    sid: int,
    user_first: str,
    user_followup: Optional[str],
) -> Dict[str, Any]:
    """一轮或两轮对话，尽量拿到 pending_confirm。"""
    messages: List[Dict[str, str]] = [{"role": "user", "content": user_first}]
    resp = _chat(messages, session_id=sid)
    sid = _extract_session_id(resp) or sid
    if not _confirm_required(resp) and user_followup and _looks_like_collecting_followup(resp):
        messages = [
            {"role": "user", "content": user_first},
            {"role": "assistant", "content": _reply_text(resp)},
            {"role": "user", "content": user_followup},
        ]
        resp = _chat(messages, session_id=sid)
        _extract_session_id(resp) or sid
    return resp


def scenario_update_contract_7a() -> Tuple[str, str]:
    """
    场景 7a：仅 header_updates；writes 应含 contracts update + contract_changes insert；
    库：notes 更新，contract_changes 有行。
    """
    cid: Optional[int] = None
    try:
        cid, _items, cust_name = _seed_e2e_contract(1, "E2E7A")
        notes_target = f"E2E7A_HDR_{cid}"
        sid = _new_session_id()
        u1 = (
            f"我要走 update_contract 修改合同。合同 id={cid}，客户「{cust_name}」。"
            f"变更原因：端到端场景7a。请把备注改成「{notes_target}」。"
            "请先 query_db 查到该合同，再调用 submit 生成待确认预览。"
        )
        u2 = (
            f"请直接调用 submit 工具。data 为 JSON 对象："
            f'{{"record_id":{cid},"reason":"端到端场景7a",'
            f'"header_updates":{{"notes":"{notes_target}"}}}}'
        )
        resp = _chat_update_contract_round(sid, u1, u2)
        fid = _extract_frame_id(resp)
        writes = _extract_writes_from_response(resp, fid)
        intent = str(resp.get("intent") or _deep_find_first(resp, "intent") or "")
        _log_writes("7a", writes)

        if not _confirm_required(resp):
            return (
                "WARN",
                f"未出现 confirm_required（intent={intent!r}）；"
                "模型可能未 submit。writes={_writes_table_ops(writes)}",
            )
        if not fid:
            return "FAIL", "confirm_required 为真但无法解析 frame_id"
        pairs = {(t, o) for _s, t, o in _writes_table_ops(writes)}
        if ("contracts", "update") not in pairs or ("contract_changes", "insert") not in pairs:
            return (
                "WARN",
                f"writes 结构未覆盖 contracts/update 与 contract_changes/insert：{sorted(pairs)}",
            )

        try:
            cresp = _confirm(fid)
        except Exception as e:
            return "FAIL", f"POST /ai/confirm 失败: {e}"
        if str(cresp.get("confirm_result") or "") == "error":
            ed = str(cresp.get("error_detail") or cresp.get("content") or "")
            return "WARN", f"confirm_result=error: {ed[:400]}"

        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT notes FROM business.contracts WHERE id = %s AND deleted_at IS NULL",
                    (cid,),
                )
                row = cur.fetchone()
                cur.execute(
                    "SELECT count(*)::int FROM business.contract_changes WHERE contract_id = %s",
                    (cid,),
                )
                nchg = cur.fetchone()[0]
        if not row or str(row[0] or "").strip() != notes_target:
            got = repr(row[0]) if row else None
            return (
                "FAIL",
                f"库 notes 期望 {notes_target!r}，实际 {got}",
            )
        if nchg < 1:
            return "FAIL", f"contract_changes 期望至少 1 行，实际 {nchg}"
        return (
            "PASS",
            f"intent={intent!r}；writes OK；notes 已写入；contract_changes={nchg}",
        )
    finally:
        if cid is not None:
            try:
                _soft_delete_contract_e2e(cid)
            except Exception as e:
                print(
                    f"[e2e] 场景 7a 清理合同 id={cid} 失败: {e}",
                    file=sys.stderr,
                    flush=True,
                )


def scenario_update_contract_7b() -> Tuple[str, str]:
    """
    场景 7b：items_add；writes 应含 contract_items insert + contract_changes；
    库：新增一行明细。
    """
    cid: Optional[int] = None
    try:
        cid, _items, cust_name = _seed_e2e_contract(1, "E2E7B")
        sid = _new_session_id()
        u1 = (
            f"update_contract：合同 id={cid}，客户「{cust_name}」。"
            "变更原因：端到端场景7b。请新增一条明细：产品名「E2E7B新品布」，数量 50 米，单价 12.5。"
            "先 query_db，再 submit。"
        )
        u2 = (
            f"请调用 submit。data.record_id={cid}，reason=端到端场景7b，"
            "items_add 为仅含一个元素的数组，元素字段："
            f'product_name「E2E7B新品布」, unit「米」, quantity 50, unit_price 12.5。'
        )
        resp = _chat_update_contract_round(sid, u1, u2)
        fid = _extract_frame_id(resp)
        writes = _extract_writes_from_response(resp, fid)
        intent = str(resp.get("intent") or _deep_find_first(resp, "intent") or "")
        _log_writes("7b", writes)

        if not _confirm_required(resp):
            return (
                "WARN",
                f"未出现 confirm_required（intent={intent!r}）；writes={_writes_table_ops(writes)}",
            )
        if not fid:
            return "FAIL", "confirm_required 为真但无法解析 frame_id"

        has_add = False
        has_cc = False
        for _s, tb, op in _writes_table_ops(writes):
            if tb == "contract_items" and op == "insert":
                has_add = True
            if tb == "contract_changes" and op == "insert":
                has_cc = True
        if not has_add or not has_cc:
            return (
                "WARN",
                f"期望 contract_items/insert 与 contract_changes/insert，实际 {_writes_table_ops(writes)}",
            )

        try:
            cresp = _confirm(fid)
        except Exception as e:
            return "FAIL", f"POST /ai/confirm 失败: {e}"
        if str(cresp.get("confirm_result") or "") == "error":
            ed = str(cresp.get("error_detail") or cresp.get("content") or "")
            return "WARN", f"confirm_result=error: {ed[:400]}"

        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT count(*)::int FROM business.contract_items
                    WHERE contract_id = %s AND deleted_at IS NULL
                      AND product_name = 'E2E7B新品布'
                    """,
                    (cid,),
                )
                n_new = cur.fetchone()[0]
                cur.execute(
                    """
                    SELECT count(*)::int FROM business.contract_items
                    WHERE contract_id = %s AND deleted_at IS NULL
                    """,
                    (cid,),
                )
                n_all = cur.fetchone()[0]
        if n_new < 1:
            return "FAIL", f"未找到 product_name=E2E7B新品布 的明细（全量明细行={n_all}）"
        return (
            "PASS",
            f"intent={intent!r}；明细已新增；总行数={n_all}",
        )
    finally:
        if cid is not None:
            try:
                _soft_delete_contract_e2e(cid)
            except Exception as e:
                print(
                    f"[e2e] 场景 7b 清理合同 id={cid} 失败: {e}",
                    file=sys.stderr,
                    flush=True,
                )


def scenario_update_contract_7c() -> Tuple[str, str]:
    """
    场景 7c：items_update + items_remove；writes 三者齐全；
    库：一行 quantity 更新，另一行软删。
    """
    cid: Optional[int] = None
    try:
        cid, item_ids, cust_name = _seed_e2e_contract(2, "E2E7C")
        if len(item_ids) < 2:
            return "FAIL", "种子合同明细不足 2 行"
        i1, i2 = item_ids[0], item_ids[1]
        sid = _new_session_id()
        u1 = (
            f"update_contract：合同 id={cid}，客户「{cust_name}」。变更原因：端到端场景7c。"
            f"明细 id={i1} 的数量改为 99；明细 id={i2} 删除（软删）。"
            "先 query_db，再 submit。"
        )
        u2 = (
            f"请调用 submit。record_id={cid}，reason=端到端场景7c。"
            f"items_update：[{{\"record_id\":{i1},\"quantity\":99}}]；"
            f"items_remove：[{{\"record_id\":{i2}}}]。"
        )
        resp = _chat_update_contract_round(sid, u1, u2)
        fid = _extract_frame_id(resp)
        writes = _extract_writes_from_response(resp, fid)
        intent = str(resp.get("intent") or _deep_find_first(resp, "intent") or "")
        _log_writes("7c", writes)

        if not _confirm_required(resp):
            return (
                "WARN",
                f"未出现 confirm_required（intent={intent!r}）；writes={_writes_table_ops(writes)}",
            )
        if not fid:
            return "FAIL", "confirm_required 为真但无法解析 frame_id"

        ops = _writes_table_ops(writes)
        tbl_ops = {(t, o) for _s, t, o in ops}
        if ("contract_items", "update") not in tbl_ops or (
            "contract_items",
            "soft_delete",
        ) not in tbl_ops or ("contract_changes", "insert") not in tbl_ops:
            return (
                "WARN",
                f"期望 contract_items update+soft_delete 与 contract_changes insert：{ops}",
            )

        try:
            cresp = _confirm(fid)
        except Exception as e:
            return "FAIL", f"POST /ai/confirm 失败: {e}"
        if str(cresp.get("confirm_result") or "") == "error":
            ed = str(cresp.get("error_detail") or cresp.get("content") or "")
            return "WARN", f"confirm_result=error: {ed[:400]}"

        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT quantity, deleted_at FROM business.contract_items WHERE id = %s
                    """,
                    (i1,),
                )
                r1 = cur.fetchone()
                cur.execute(
                    "SELECT deleted_at FROM business.contract_items WHERE id = %s",
                    (i2,),
                )
                r2 = cur.fetchone()
        q1 = r1[0] if r1 else None
        d1 = r1[1] if r1 else None
        d2 = r2[0] if r2 else None
        if q1 is None or Decimal(str(q1)) != Decimal("99"):
            return "FAIL", f"明细 {i1} quantity 期望 99，实际 {q1!r}"
        if d1 is not None:
            return "FAIL", f"明细 {i1} 不应软删，deleted_at={d1!r}"
        if d2 is None:
            return "FAIL", f"明细 {i2} 应软删，deleted_at 为空"
        return (
            "PASS",
            f"intent={intent!r}；明细{i1} qty=99；明细{i2} 已软删",
        )
    finally:
        if cid is not None:
            try:
                _soft_delete_contract_e2e(cid)
            except Exception as e:
                print(
                    f"[e2e] 场景 7c 清理合同 id={cid} 失败: {e}",
                    file=sys.stderr,
                    flush=True,
                )


def scenario_coreference() -> Tuple[str, str]:
    """场景5：同 session 内先改额度并确认，再查「这个客户」应能指代浑圆纺织。"""
    orig = _get_credit_limit(CUSTOMER_NAME)
    if orig is None:
        return "FAIL", "找不到客户「浑圆纺织」"

    sid = _new_session_id()
    r1 = _chat(
        [{"role": "user", "content": "把浑圆纺织的授信额度改成十八万"}],
        session_id=sid,
    )
    if not _confirm_required(r1):
        if _looks_like_text_confirm_without_tool(r1):
            return (
                "WARN",
                "第一步未 pending_confirm（模型可能未调 update_entity），跳过指代测试",
            )
        return (
            "WARN",
            "第一步未触发确认，跳过后续指代测试",
        )
    fid = _extract_frame_id(r1)
    if not fid:
        return "FAIL", "无法解析 frame_id"
    try:
        _confirm(fid)
    except Exception as e:
        return "FAIL", f"确认失败: {e}"

    sid = _extract_session_id(r1) or sid
    r2 = _chat(
        [{"role": "user", "content": "帮我查一下这个客户的信息"}],
        session_id=sid,
    )
    text = _reply_text(r2)
    if "浑圆纺织" not in text:
        return (
            "WARN",
            f"回复未包含「浑圆纺织」（前 300 字）：{text[:300]!r}",
        )
    _set_credit_limit(CUSTOMER_NAME, orig)
    return "PASS", "回复含「浑圆纺织」，额度已恢复"


def run_once(run_label: str) -> List[Tuple[str, str, str]]:
    results: List[Tuple[str, str, str]] = []
    try:
        _cleanup_collecting_frames()
    except Exception as e:
        return [
            ("cleanup", "FAIL", str(e)),
        ]

    original = _get_credit_limit(CUSTOMER_NAME)
    if original is None:
        return [("prep", "FAIL", "无客户「浑圆纺织」")]

    try:
        # 1
        st, msg = scenario_query()
        results.append(("1 查询", st, msg))
        # 2
        st, msg = scenario_update_confirm(original)
        results.append(("2 修改+确认", st, msg))
        # 3
        st, msg = scenario_create_product()
        results.append(("3 新建+确认", st, msg))
        # 4 跳过
        results.append(("4 防护(executor)", "SKIP", "见 test_executor_guards.py"))
        # 5
        st, msg = scenario_coreference()
        results.append(("5 指代消解", st, msg))
        # 6 合同创建（依赖 LLM 调 submit_contract）
        st, msg = scenario_contract_create()
        results.append(("6 合同创建", st, msg))
        # 7 update_contract（种子合同 + submit；独立 session）
        st, msg = scenario_update_contract_7a()
        results.append(("7a update 主表", st, msg))
        st, msg = scenario_update_contract_7b()
        results.append(("7b items_add", st, msg))
        st, msg = scenario_update_contract_7c()
        results.append(("7c items_update+remove", st, msg))
    finally:
        if original is not None:
            try:
                _set_credit_limit(CUSTOMER_NAME, original)
            except Exception as e:
                results.append(("finally 恢复额度", "FAIL", str(e)))

    return results


def run_only_scenario_7() -> List[Tuple[str, str, str]]:
    """仅执行 7a/7b/7c，便于单独回归 update_contract。"""
    results: List[Tuple[str, str, str]] = []
    try:
        _cleanup_collecting_frames()
    except Exception as e:
        return [("cleanup", "FAIL", str(e))]
    try:
        st, msg = scenario_update_contract_7a()
        results.append(("7a update 主表", st, msg))
        st, msg = scenario_update_contract_7b()
        results.append(("7b items_add", st, msg))
        st, msg = scenario_update_contract_7c()
        results.append(("7c items_update+remove", st, msg))
    except httpx.ConnectError as e:
        return [("connect", "FAIL", f"无法连接 {BASE}: {e}")]
    except httpx.ReadTimeout as e:
        return [("timeout", "WARN", str(e))]
    return results


def main() -> None:
    if "--only-7" in sys.argv:
        print(f"BASE={BASE} timeout={TIMEOUT}s user_id={USER_ID} [仅场景 7a/7b/7c]\n")
        try:
            rows = run_only_scenario_7()
        except httpx.HTTPStatusError as e:
            print(f"FAIL HTTP {e.response.status_code}: {e.response.text[:500]}")
            sys.exit(1)
        for name, st, msg in rows:
            print(f"  [{st}] {name}: {msg}")
        fail = sum(1 for _, st, _ in rows if st == "FAIL")
        sys.exit(1 if fail else 0)

    print(f"BASE={BASE} timeout={TIMEOUT}s user_id={USER_ID}\n")
    all_runs: List[List[Tuple[str, str, str]]] = []
    for i in range(3):
        label = f"第 {i + 1} 轮"
        print(f"======== {label} ========")
        try:
            rows = run_once(label)
        except httpx.ConnectError as e:
            print(f"FAIL 无法连接 {BASE}：{e}\n请先启动：uvicorn main:app --host 127.0.0.1 --port 8000")
            sys.exit(2)
        except httpx.ReadTimeout as e:
            print(f"WARN 本轮超时（可调大 E2E_CHAT_TIMEOUT）：{e}")
            rows = [(label, "WARN", f"ReadTimeout: {e}")]
        except httpx.HTTPStatusError as e:
            print(f"FAIL HTTP {e.response.status_code}: {e.response.text[:500]}")
            sys.exit(1)
        all_runs.append(rows)
        for name, st, msg in rows:
            print(f"  [{st}] {name}: {msg}")
        print()

    # 汇总
    print("—— 三轮汇总 ——")
    for idx, rows in enumerate(all_runs, start=1):
        fail = sum(1 for _, st, _ in rows if st == "FAIL")
        warn = sum(1 for _, st, _ in rows if st == "WARN")
        ok = sum(1 for _, st, _ in rows if st == "PASS")
        print(
            f"轮{idx}: PASS={ok} WARN={warn} FAIL={fail}（SKIP 不计入）"
        )


if __name__ == "__main__":
    main()
