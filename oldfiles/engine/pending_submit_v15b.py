"""V15-D：execute_submit 由 workflow_steps + 通用清洗驱动；update_contract 走同一循环。"""
from __future__ import annotations

import copy
import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

from psycopg2.extras import RealDictCursor

from engine.tool_executor import _decimal_or_error

_SIMPLE_ENTITY_INTENTS = frozenset(
    {"create_simple_entity", "update_simple_entity", "delete_simple_entity"}
)

# 合同三意图：写 contracts / contract_items / contract_changes 时不走 simple 表白名单
# （build_col_vals_from_fields / collect_update_fields_validated 仅覆盖 entity_type=simple）
_CONTRACT_INTENTS = frozenset(
    {"create_contract", "update_contract", "delete_contract"}
)


def _use_simple_table_whitelist(intent: str, table: str) -> bool:
    """
    True：对目标表做 simple 表 + meta 字段白名单校验（insert/update 前置）。
    False：合同意图写入业务合同表，改由合同归一化 + sanitize_step_data，不再做 simple 白名单拦截。
    """
    il = (intent or "").strip().lower()
    t = (table or "").strip()
    if il in _CONTRACT_INTENTS and t in (
        "contracts",
        "contract_items",
        "contract_changes",
    ):
        return False
    return True

from engine.collecting import (
    _row_jsonable,
    build_col_vals_from_fields,
    collect_update_fields_validated,
    get_frame_verified_record_id,
)
from engine.data_sanitize import sanitize_step_data
from engine.workflow_steps_util import (
    infer_operation_from_intent,
    infer_write_operation,
    load_workflow_steps_for_intent,
)
from tools.database import db_conn, db_conn_llm

logger = logging.getLogger(__name__)


def _load_frame_workflow_intent(fid: int) -> str:
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT workflow_intent FROM business.frames WHERE id = %s LIMIT 1",
                    (int(fid),),
                )
                row = cur.fetchone()
        if not row or row[0] is None:
            return ""
        return str(row[0]).strip()
    except Exception:
        logger.warning("读取 frame.workflow_intent 失败", exc_info=True)
        return ""


def _load_frame_target_table(fid: int) -> str:
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT context_snapshot->>'target_table'
                    FROM business.frames WHERE id = %s LIMIT 1
                    """,
                    (int(fid),),
                )
                row = cur.fetchone()
        if not row or row[0] is None:
            return ""
        return str(row[0]).strip()
    except Exception:
        logger.warning("读取 frame.target_table 失败", exc_info=True)
        return ""


def _infer_table_from_data_keys(d: Dict[str, Any]) -> Optional[str]:
    """从 data 中出现的业务字段名推断表（fallback，优先用显式 table_name / frame.target_table）。"""
    skip = frozenset(
        {
            "table_name",
            "table",
            "frame_id",
            "explanation",
            "reason",
            "data",
            "record_id",
            "id",
        }
    )
    biz = {k for k in d.keys() if k not in skip and not str(k).startswith("_")}
    if not biz:
        return None
    if biz & {
        "product_code",
        "product_name",
        "category",
        "width_cm",
        "weight_gsm",
        "composition",
        "material",
        "process_desc",
        "min_order_qty",
    }:
        return "products"
    if biz & {"company_type", "is_primary", "wechat", "department"}:
        return "contacts"
    if biz & {"customer_code", "credit_limit", "tax_id", "invoice_type", "district"}:
        return "customers"
    if "name" in biz and "product_name" not in biz:
        return "customers"
    return None


def _normalize_simple_entity_work(
    work: Dict[str, Any], fid: int, intent: str
) -> Optional[str]:
    """
    将 LLM 扁平 data 转为 execute_write_entity / update_entity / delete_entity 期望的
    table_name + fields + record_id。仅 simple_entity 三意图执行。
    返回错误文案或 None。
    """
    il = (intent or "").strip().lower()
    if il not in _SIMPLE_ENTITY_INTENTS:
        return None

    expl = str(work.get("explanation") or "").strip()
    if not expl and isinstance(work.get("reason"), str):
        expl = work.get("reason", "").strip()

    table = str(work.get("table_name") or "").strip() or str(work.get("table") or "").strip()
    work.pop("table", None)
    if not table:
        table = _load_frame_target_table(fid)
    if not table:
        table = _infer_table_from_data_keys(work) or ""
    if not table:
        return "缺少目标表。"

    META = frozenset(
        {"table_name", "table", "frame_id", "explanation", "reason", "data"}
    )
    rid_raw = work.get("record_id")
    if rid_raw is None:
        rid_raw = work.get("id")

    field_vals: Dict[str, Any] = {}
    for k, v in list(work.items()):
        if k in META or str(k).startswith("_"):
            continue
        if k in ("record_id", "id"):
            continue
        field_vals[k] = v

    if "create" in il:
        work.clear()
        work["table_name"] = table
        work["fields"] = field_vals
        if expl:
            work["explanation"] = expl
        return None

    if "update" in il:
        work.clear()
        work["table_name"] = table
        work["fields"] = field_vals
        if expl:
            work["explanation"] = expl
        if rid_raw is not None:
            try:
                work["record_id"] = int(rid_raw)
            except (TypeError, ValueError):
                return "record_id 无效。"
        return None

    if "delete" in il:
        work.clear()
        work["table_name"] = table
        if expl:
            work["explanation"] = expl
        if rid_raw is not None:
            try:
                work["record_id"] = int(rid_raw)
            except (TypeError, ValueError):
                return "record_id 无效。"
        return None

    return None


def _load_contract_snapshot_for_delete(contract_id: int) -> Dict[str, Any]:
    with db_conn_llm() as conn:
        conn.autocommit = True
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM business.contracts WHERE id = %s AND deleted_at IS NULL",
                (int(contract_id),),
            )
            c = cur.fetchone()
            cur.execute(
                """
                SELECT * FROM business.contract_items
                WHERE contract_id = %s AND deleted_at IS NULL
                ORDER BY id
                """,
                (int(contract_id),),
            )
            items = cur.fetchall()
    return {
        "contract": _row_jsonable(dict(c)) if c else {},
        "items": [_row_jsonable(dict(r)) for r in items],
    }


def _write_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        s
        for s in steps
        if str(s.get("action") or "").strip().lower() == "write"
    ]


def _step_needs_submit_contract_validation(step: Dict[str, Any]) -> bool:
    tn = (step.get("table_name") or "").strip()
    dk = (step.get("data_key") or "").strip()
    return tn == "contracts" and dk == "contract"


_CONTRACT_TOP_KEYS = frozenset(
    {
        "customer_id",
        "customer_name",
        "currency",
        "payment_terms",
        "notes",
        "delivery_date",
        "signed_date",
        "template_id",
    }
)


def _normalize_and_validate_contract_work(work: Dict[str, Any]) -> Optional[str]:
    """
    将扁平 submit data 归并为 contract/items，并按业务规则校验（与 execute_submit_contract 一致），
    就地写回 work。不调用 execute_submit_contract，避免与 pending_submit_v15b 双路径冲突。
    """
    c_in = work.get("contract")
    items_in = work.get("items")
    if not isinstance(c_in, dict):
        c_in = {}
        for k in _CONTRACT_TOP_KEYS:
            if k in work:
                c_in[k] = work[k]
        if c_in:
            work["contract"] = c_in
            c_in = work["contract"]
    if not isinstance(items_in, list) or len(items_in) < 1:
        return (
            "新建合同需要 contract 对象与 items 数组（至少一行）；"
            "也可将 customer_id、customer_name、items 等放在 data 顶层。"
        )

    contract = c_in if isinstance(c_in, dict) else {}
    items = items_in

    cid = contract.get("customer_id")
    try:
        cid_int = int(cid)
    except (TypeError, ValueError):
        return "contract.customer_id 必须是整数。"
    if cid_int < 1:
        return "contract.customer_id 无效。"

    cname = contract.get("customer_name")
    if not isinstance(cname, str) or not cname.strip():
        return "contract.customer_name 必填。"

    currency = contract.get("currency")
    if currency is None or (isinstance(currency, str) and not currency.strip()):
        currency = "CNY"
    elif not isinstance(currency, str):
        return "contract.currency 必须是字符串。"
    else:
        currency = currency.strip()

    payment_terms = contract.get("payment_terms")
    if payment_terms is not None and not isinstance(payment_terms, str):
        return "contract.payment_terms 必须是字符串。"

    notes = contract.get("notes")
    if notes is not None and not isinstance(notes, str):
        return "contract.notes 必须是字符串。"

    delivery_date = contract.get("delivery_date")
    signed_date = contract.get("signed_date")
    for lab, v in (
        ("contract.delivery_date", delivery_date),
        ("contract.signed_date", signed_date),
    ):
        if v is not None and not isinstance(v, str):
            return f"{lab} 必须是字符串（日期）或省略。"

    out_items: List[Dict[str, Any]] = []
    total = Decimal("0")
    for i, raw in enumerate(items):
        if not isinstance(raw, dict):
            return f"items[{i}] 必须是对象。"
        pname = raw.get("product_name")
        if not isinstance(pname, str) or not pname.strip():
            return f"items[{i}].product_name 必填。"

        q, err = _decimal_or_error(raw.get("quantity"), f"items[{i}].quantity")
        if err:
            return err
        assert q is not None
        if q <= 0:
            return f"items[{i}].quantity 必须大于 0。"

        up, err2 = _decimal_or_error(raw.get("unit_price"), f"items[{i}].unit_price")
        if err2:
            return err2
        assert up is not None
        if up < 0:
            return f"items[{i}].unit_price 不能为负。"

        amt = q * up
        total += amt

        line: Dict[str, Any] = {
            "product_name": pname.strip(),
            "quantity": float(q),
            "unit_price": float(up),
            "amount": float(amt),
        }
        pc = raw.get("product_code")
        if isinstance(pc, str) and pc.strip():
            line["product_code"] = pc.strip()
        pid = raw.get("product_id")
        if pid is not None:
            try:
                line["product_id"] = int(pid)
            except (TypeError, ValueError):
                return f"items[{i}].product_id 必须是整数或省略。"
        for opt_key in ("unit", "color", "spec", "notes"):
            ov = raw.get(opt_key)
            if ov is None:
                continue
            if not isinstance(ov, str):
                return f"items[{i}].{opt_key} 必须是字符串或省略。"
            if ov.strip():
                line[opt_key] = ov.strip()

        out_items.append(line)

    contract_out: Dict[str, Any] = {
        "customer_id": cid_int,
        "customer_name": cname.strip(),
        "currency": currency,
        "total_amount": float(total),
    }
    if isinstance(payment_terms, str) and payment_terms.strip():
        contract_out["payment_terms"] = payment_terms.strip()
    if isinstance(notes, str) and notes.strip():
        contract_out["notes"] = notes.strip()
    if isinstance(delivery_date, str) and delivery_date.strip():
        contract_out["delivery_date"] = delivery_date.strip()
    if isinstance(signed_date, str) and signed_date.strip():
        contract_out["signed_date"] = signed_date.strip()

    work["contract"] = contract_out
    work["items"] = out_items
    return None


def _work_has_contract_delete_soft_delete(
    write_steps: List[Dict[str, Any]], intent: str, work: Dict[str, Any]
) -> bool:
    for s in write_steps:
        tbl = (s.get("table_name") or work.get("table_name") or "").strip()
        op = infer_operation_from_intent(intent, s)
        if tbl == "contracts" and op == "soft_delete":
            return True
    return False


def _infer_change_type_from_work(work: Dict[str, Any]) -> str:
    hu = work.get("header_updates") if isinstance(work.get("header_updates"), dict) else {}
    items_add = work.get("items_add") if isinstance(work.get("items_add"), list) else []
    items_update = work.get("items_update") if isinstance(work.get("items_update"), list) else []
    items_remove = work.get("items_remove") if isinstance(work.get("items_remove"), list) else []
    has_header = bool(hu)
    has_items = bool(items_add or items_update or items_remove)
    if has_header and has_items:
        return "update_both"
    if has_header:
        return "update_header"
    return "update_items"


def _build_diff_summary_from_work(work: Dict[str, Any]) -> Dict[str, Any]:
    hu = work.get("header_updates") if isinstance(work.get("header_updates"), dict) else {}
    items_add = work.get("items_add") if isinstance(work.get("items_add"), list) else []
    items_update = work.get("items_update") if isinstance(work.get("items_update"), list) else []
    items_remove = work.get("items_remove") if isinstance(work.get("items_remove"), list) else []
    return {
        "header_keys": list(hu.keys()),
        "items_add": len([x for x in items_add if isinstance(x, dict)]),
        "items_update": len([x for x in items_update if isinstance(x, dict)]),
        "items_remove": len(items_remove),
    }


def _prevalidate_submit_contract(
    work: Dict[str, Any], fid: int, write_steps: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    if not any(_step_needs_submit_contract_validation(s) for s in write_steps):
        return None
    err = _normalize_and_validate_contract_work(work)
    if err:
        return {"error": err}
    logger.info("[pending_submit_v15b] branch=contract_work_normalized_ok")
    return None


def _prevalidate_single_step_entity(
    work: Dict[str, Any],
    fid: int,
    intent: str,
    write_steps: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if len(write_steps) != 1:
        return None
    st = write_steps[0]
    if (st.get("data_key") or "").strip():
        return None
    op = infer_operation_from_intent(intent, st)
    expl = (work.get("explanation") or "").strip() if isinstance(work.get("explanation"), str) else ""
    if op == "insert":
        from engine.tool_executor import execute_write_entity

        r = execute_write_entity(
            (work.get("table_name") or "").strip(),
            work.get("fields") if isinstance(work.get("fields"), dict) else {},
            expl,
            fid,
        )
        if r.get("error"):
            return {"error": r["error"]}
        pv = r.get("preview") or {}
        work["fields"] = pv.get("row") if isinstance(pv.get("row"), dict) else {}
        work["table_name"] = (pv.get("table_name") or work.get("table_name") or "").strip()
        return r
    if op == "update":
        from engine.tool_executor import execute_update_entity

        r = execute_update_entity(
            (work.get("table_name") or "").strip(),
            work.get("record_id"),
            work.get("fields") if isinstance(work.get("fields"), dict) else {},
            (work.get("explanation") or "").strip()
            if isinstance(work.get("explanation"), str)
            else "",
            fid,
        )
        if r.get("error"):
            return {"error": r["error"]}
        pu = r.get("pending_update") or {}
        work["table_name"] = (pu.get("table_name") or "").strip()
        work["record_id"] = pu.get("record_id")
        work["fields"] = pu.get("fields") if isinstance(pu.get("fields"), dict) else {}
        return r
    if op == "soft_delete":
        from engine.tool_executor import execute_delete_entity

        tn = (st.get("table_name") or work.get("table_name") or "").strip()
        r = execute_delete_entity(
            tn,
            work.get("record_id"),
            (work.get("explanation") or "").strip(),
            fid,
        )
        if r.get("error"):
            return {"error": r["error"]}
        pd = r.get("pending_delete") or {}
        work["table_name"] = (pd.get("table_name") or tn).strip()
        work["record_id"] = pd.get("record_id")
        work["_delete_reason"] = (pd.get("reason") or "").strip()
        return r
    return None


def _prevalidate_contract_delete(
    work: Dict[str, Any], fid: int, intent: str, write_steps: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    if len(write_steps) <= 1:
        return None
    if not _work_has_contract_delete_soft_delete(write_steps, intent, work):
        return None
    from engine.tool_executor import execute_delete_entity

    expl = (work.get("explanation") or work.get("reason") or "").strip()
    r = execute_delete_entity(
        "contracts",
        work.get("record_id"),
        expl,
        fid,
    )
    if r.get("error"):
        return {"error": r["error"]}
    pd = r.get("pending_delete") or {}
    try:
        rid = int(pd.get("record_id"))
    except (TypeError, ValueError):
        return {"error": "无效的合同 record_id。"}
    work["record_id"] = rid
    work["_delete_reason"] = (pd.get("reason") or expl or "").strip()
    return r


def _validate_update_contract_submit(work: Dict[str, Any], fid: int, reason: str) -> Optional[str]:
    rsn = (reason or "").strip() or (work.get("reason") or "").strip()
    if not rsn:
        return "必须提供变更原因 reason。"
    rid = work.get("record_id") if work.get("record_id") is not None else work.get("contract_id")
    if rid is None:
        rid = get_frame_verified_record_id(fid)
    if rid is None:
        return "请提供 data.record_id 或先用 query_db 锁定合同（唯一一行含 id）。"
    try:
        work["record_id"] = int(rid)
    except (TypeError, ValueError):
        return "合同 record_id 无效。"
    hu = work.get("header_updates") if isinstance(work.get("header_updates"), dict) else {}
    items_add = work.get("items_add") if isinstance(work.get("items_add"), list) else []
    items_update = work.get("items_update") if isinstance(work.get("items_update"), list) else []
    items_remove = work.get("items_remove") if isinstance(work.get("items_remove"), list) else []
    if not hu and not items_add and not items_update and not items_remove:
        return "没有任何变更内容。"
    return None


def _normalize_item_id_to_record_id(rows: Any) -> None:
    if not isinstance(rows, list):
        return
    for row in rows:
        if not isinstance(row, dict):
            continue
        if "record_id" not in row and "item_id" in row:
            row["record_id"] = row.get("item_id")


def execute_submit_v15b(
    arguments: Dict[str, Any],
    frame_id: Optional[int],
    intent: str,
    reason: str,
) -> Dict[str, Any]:
    """按 workflow_steps 组装 writes（V15-D）。"""
    data = arguments.get("data")
    if not isinstance(data, dict):
        return {"error": "submit 需要参数 data（对象）。"}
    fid = int(frame_id)
    steps = load_workflow_steps_for_intent(intent)
    ws = _write_steps(steps)
    if not ws:
        return {"error": f"意图 {intent!r} 没有 write 步骤。"}

    work: Dict[str, Any] = copy.deepcopy(data)
    preview_merge: Dict[str, Any] = {}
    il = (intent or "").strip().lower()

    if isinstance(reason, str) and reason.strip():
        if not str(work.get("explanation") or "").strip():
            work["explanation"] = reason.strip()

    err_se = _normalize_simple_entity_work(work, fid, intent)
    if err_se:
        return {"error": err_se}

    if il == "update_contract":
        err_uc = _validate_update_contract_submit(work, fid, reason)
        if err_uc:
            return {"error": err_uc}
        _normalize_item_id_to_record_id(work.get("items_update"))

    r_del = _prevalidate_contract_delete(work, fid, intent, ws)
    if r_del and r_del.get("error"):
        return r_del
    if r_del and not r_del.get("error"):
        preview_merge = {**preview_merge, **(r_del.get("preview") or {})}

    r_sc = _prevalidate_submit_contract(work, fid, ws)
    if isinstance(r_sc, dict) and r_sc.get("error"):
        return r_sc
    if r_sc:
        preview_merge = {**preview_merge, **(r_sc.get("preview") or {})}

    r_single = _prevalidate_single_step_entity(work, fid, intent, ws)
    if isinstance(r_single, dict) and r_single.get("error"):
        return r_single
    if r_single:
        preview_merge = {**preview_merge, **(r_single.get("preview") or {})}

    contract_rid = work.get("record_id") or work.get("id")

    writes: List[Dict[str, Any]] = []
    for step in ws:
        table = (step.get("table_name") or work.get("table_name") or "").strip()
        operation = infer_write_operation(intent, step)
        data_key = (step.get("data_key") or "").strip()
        seq = step.get("seq")
        depends_on = step.get("depends_on")
        if isinstance(depends_on, list):
            depends_on = [int(x) for x in depends_on if x is not None]
        else:
            depends_on = None

        wf_raw = step.get("where_field")
        where_field = (str(wf_raw).strip() if wf_raw else "") or "id"

        if data_key and data_key in work:
            step_data: Any = work[data_key]
        elif data_key:
            step_data = {}
        elif table == "contract_changes" and il == "update_contract":
            step_data = {}
        else:
            if operation == "insert":
                if il in _SIMPLE_ENTITY_INTENTS and isinstance(
                    work.get("fields"), dict
                ):
                    step_data = dict(work["fields"])
                else:
                    step_data = (
                        work.get("fields")
                        if isinstance(work.get("fields"), dict)
                        else work
                    )
            elif operation == "update":
                if il in _SIMPLE_ENTITY_INTENTS and isinstance(
                    work.get("fields"), dict
                ):
                    step_data = dict(work["fields"])
                    rid = work.get("record_id")
                    if rid is not None:
                        step_data["record_id"] = int(rid)
                else:
                    step_data = {
                        k: v
                        for k, v in work.items()
                        if k not in ("explanation",) and not str(k).startswith("_")
                    }
            else:
                step_data = {
                    k: v
                    for k, v in work.items()
                    if k not in ("explanation",) and not str(k).startswith("_")
                }

        if not isinstance(step_data, (dict, list)) and step_data is not None:
            step_data = {}

        is_uc_audit = il == "update_contract" and table == "contract_changes"
        if not is_uc_audit:
            if not step_data:
                continue
            if isinstance(step_data, list) and len(step_data) == 0:
                continue
            if isinstance(step_data, dict) and len(step_data) == 0:
                continue

        if il == "update_contract" and table == "contracts" and data_key == "header_updates":
            if isinstance(step_data, dict):
                hdr = dict(step_data)
                if contract_rid is not None:
                    hdr["record_id"] = int(contract_rid)
                step_data = hdr

        if il == "update_contract" and table == "contract_items" and data_key == "items_add":
            if isinstance(step_data, list) and contract_rid is not None:
                step_data = [
                    {**dict(r), "contract_id": int(contract_rid)}
                    if isinstance(r, dict)
                    else r
                    for r in step_data
                ]

        if operation == "soft_delete" and isinstance(step_data, dict):
            sd = dict(step_data)
            if where_field != "id" and where_field not in sd:
                rid = work.get("record_id") or work.get("id")
                if rid is not None:
                    sd[where_field] = rid
            elif (
                where_field == "id"
                and "record_id" not in sd
                and "id" not in sd
            ):
                rid = work.get("record_id") or work.get("id")
                if rid is not None:
                    sd["record_id"] = rid
            step_data = sd

        if table == "contract_changes" and operation == "insert":
            if not isinstance(step_data, dict):
                step_data = {}
            cd = dict(step_data)
            if il == "update_contract":
                rid = int(work["record_id"])
                cd.setdefault("contract_id", rid)
                cd.setdefault("reason", (reason or work.get("reason") or "").strip())
                cd.setdefault("change_type", _infer_change_type_from_work(work))
                cd.setdefault("diff_summary", _build_diff_summary_from_work(work))
                cd.setdefault("evidence_refs", [])
                if not cd.get("snapshot_before"):
                    cd["snapshot_before"] = _load_contract_snapshot_for_delete(rid)
                cd.setdefault("snapshot_after", {})
            else:
                if "reason" not in cd and reason:
                    cd["reason"] = reason
                if "change_type" not in cd:
                    if "delete" in il:
                        cd["change_type"] = "soft_delete"
                    elif "update" in il:
                        cd["change_type"] = "update"
                rid = work.get("record_id") or work.get("id")
                if rid and not cd.get("snapshot_before"):
                    rreason = work.get("_delete_reason") or reason
                    cd.update(
                        {
                            "contract_id": int(rid),
                            "evidence_refs": [],
                            "snapshot_before": _load_contract_snapshot_for_delete(int(rid)),
                            "snapshot_after": {"deleted": True},
                            "diff_summary": {
                                "operation": "soft_delete",
                                "reason": rreason,
                            },
                        }
                    )
            step_data = cd

        step_data = sanitize_step_data(table, step_data, operation)

        if operation == "insert" and table and table != "contract_changes":
            if _use_simple_table_whitelist(il, table):
                if isinstance(step_data, list):
                    for row in step_data:
                        if not isinstance(row, dict):
                            return {"error": "明细行须为对象。"}
                        _, err = build_col_vals_from_fields(table, row)
                        if err:
                            return {"error": err}
                elif isinstance(step_data, dict) and step_data:
                    _, err = build_col_vals_from_fields(table, step_data)
                    if err:
                        return {"error": err}
            else:
                logger.info(
                    "[pending_submit_v15b] branch=skip_simple_whitelist_insert "
                    "intent=%s table=%s seq=%s",
                    il,
                    table,
                    seq,
                )

        if operation == "update":
            if _use_simple_table_whitelist(il, table):
                if isinstance(step_data, list):
                    for row in step_data:
                        if not isinstance(row, dict):
                            return {"error": "更新行须为对象。"}
                        _, err = collect_update_fields_validated(table, row)
                        if err:
                            return {"error": err}
                elif isinstance(step_data, dict) and table:
                    _, err = collect_update_fields_validated(table, step_data)
                    if err:
                        return {"error": err}
            else:
                logger.info(
                    "[pending_submit_v15b] branch=skip_simple_whitelist_update "
                    "intent=%s table=%s seq=%s",
                    il,
                    table,
                    seq,
                )

        item: Dict[str, Any] = {
            "seq": seq,
            "table": table,
            "operation": operation,
            "data": step_data,
            "label": (step.get("label") or "")[:200],
        }
        if depends_on:
            item["depends_on"] = depends_on
        if where_field != "id":
            item["where_field"] = where_field
            if operation == "soft_delete":
                item["require_row"] = False
        writes.append(item)

    if not writes:
        return {"error": "没有可执行的写步骤（可能数据均为空）。"}

    logger.info(
        "[pending_submit_v15b] branch=writes_assembled intent=%s writes_count=%s",
        il,
        len(writes),
    )

    ps: Dict[str, Any] = {"writes": writes}
    if reason:
        ps["reason"] = reason
    elif work.get("reason"):
        ps["reason"] = str(work.get("reason")).strip()

    expl_out = ""
    if isinstance(r_sc, dict):
        expl_out = r_sc.get("explanation") or ""
    if isinstance(r_single, dict):
        expl_out = expl_out or r_single.get("explanation") or ""
    if isinstance(r_del, dict):
        expl_out = expl_out or r_del.get("explanation") or ""

    preview = {"writes": writes, "reason": ps.get("reason", ""), **preview_merge}
    return {
        "status": "pending_confirm",
        "action": "submit_confirm",
        "preview": preview,
        "pending_submit": ps,
        "explanation": expl_out,
        "frame_id": fid,
    }


def execute_submit_with_arguments(
    arguments: Dict[str, Any], frame_id: Optional[int]
) -> Dict[str, Any]:
    """tool_executor.execute_submit 入口：解析 frame_id / reason / intent 后调 V15-D。"""
    data = arguments.get("data")
    if not isinstance(data, dict):
        return {"error": "submit 需要参数 data（对象）。"}
    reason = ""
    if isinstance(arguments.get("reason"), str):
        reason = arguments.get("reason").strip()
    fid_arg = arguments.get("frame_id")
    if frame_id is None and fid_arg is not None:
        try:
            frame_id = int(fid_arg)
        except (TypeError, ValueError):
            return {"error": "frame_id 无效。"}
    if frame_id is None:
        return {"error": "submit 需要 frame_id。"}
    fid = int(frame_id)
    intent = _load_frame_workflow_intent(fid)
    logger.info("[executor.submit] frame=%s intent=%s data_keys=%s", fid, intent, list(data.keys()))
    return execute_submit_v15b(arguments, fid, intent, reason)
