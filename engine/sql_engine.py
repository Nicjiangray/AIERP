"""
统一 SQL 引擎：安全审计后执行 SELECT，或对写 SQL 做预览摘要。
替代面向结构化 payload 的 query/write 执行路径（query_engine / write_engine 仍保留为 deprecated）。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

import bcrypt

logger = logging.getLogger(__name__)

# 拥有全表权限的角色（与 meta.role_permissions 中 admin 种子一致；含历史 super_admin）
_PRIVILEGED_ROLES = frozenset({"admin", "super_admin"})

# INSERT 成功后需自动建审批单的业务表 → meta.approval_rules.target_type
_TABLE_TO_APPROVAL_TARGET: Dict[str, str] = {
    "business.contracts": "contract",
    "business.purchase_orders": "purchase_order",
}

import sqlparse
from sqlparse.sql import Statement

from psycopg2.extras import RealDictCursor

from tools.database import db_conn

from engine.incident_logger import is_audit_permission_only, log_incident
from engine.write_engine import translate_db_write_exception


def _cursor_execute(cur, sql: str, params: List[Any]) -> None:
    """
    psycopg2：第二参为空列表时仍会按 pyformat 解析 SQL 中的 %。
    LIKE '%x%' 中的 % 会触发错误（如 list index out of range）。无参时必须只传 SQL 字符串。
    """
    if params:
        cur.execute(sql, params)
    else:
        cur.execute(sql)


_FORBIDDEN_TOP_KEYWORDS = frozenset(
    {
        "DROP",
        "ALTER",
        "TRUNCATE",
        "CREATE",
        "GRANT",
        "REVOKE",
    }
)

DANGER_KEYWORDS = ["删除", "DELETE", "修改", "UPDATE", "覆盖", "替换", "清空"]


def highlight_words_from_summary(summary: str) -> List[str]:
    """扫描摘要文本，返回摘要中出现的危险词列表（用于前端高亮）。"""
    if not summary:
        return []
    out: List[str] = []
    for w in DANGER_KEYWORDS:
        if w in summary:
            out.append(w)
    return out


def _load_table_whitelist(cur) -> Set[str]:
    """从 meta + information_schema 解析 schema.table，且 include_in_domain_model=true。"""
    cur.execute(
        """
        SELECT DISTINCT LOWER(t.table_schema || '.' || t.table_name)
        FROM meta.meta_tables m
        JOIN information_schema.tables t
          ON t.table_name = m.table_name
         AND t.table_schema IN ('business', 'meta')
         AND t.table_type = 'BASE TABLE'
        WHERE (m.deleted_at IS NULL)
          AND m.include_in_domain_model = true
        """
    )
    return {r[0] for r in cur.fetchall()}


def _get_user_roles(conn, user_id: int) -> List[str]:
    """由 employees.id 解析账号角色（business.user_role_grants）。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT rg.role_code
            FROM business.user_role_grants rg
            JOIN business.user_accounts ua ON ua.id = rg.user_account_id
            WHERE ua.employee_id = %s AND ua.is_active = true
            """,
            (user_id,),
        )
        return [str(r[0]) for r in cur.fetchall() if r and r[0]]


def _check_table_permission(
    conn,
    user_id: Optional[int],
    roles: List[str],
    table_name: str,
    action: str,
) -> Tuple[bool, str]:
    """
    表级权限：read / write / delete。
    优先级：admin/super_admin → user_permissions（个人覆盖）→ role_permissions。
    个人覆盖有记录时仅按该记录判定，不再合并角色。
    """
    if any(r in _PRIVILEGED_ROLES for r in roles):
        return True, ""
    tn = (table_name or "").strip().lower()
    if "." not in tn:
        tn = f"business.{tn}"
    col_map = {"read": "can_read", "write": "can_write", "delete": "can_delete"}
    col = col_map.get(action)
    if not col:
        return False, f"未知操作类型：{action}"

    if user_id is not None:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {col}
                FROM business.user_permissions
                WHERE employee_id = %s AND LOWER(table_name) = %s AND deleted_at IS NULL
                LIMIT 1
                """,
                (int(user_id), tn),
            )
            row = cur.fetchone()
            if row is not None:
                allowed = row[0]
                if allowed:
                    return True, ""
                return False, f"权限不足：你对 {tn} 没有 {action} 权限"

    if not roles:
        return False, "权限不足：账号未分配角色"

    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT 1 FROM meta.role_permissions
            WHERE role_code = ANY(%s)
              AND LOWER(table_name) = %s
              AND {col} = true
            LIMIT 1
            """,
            (roles, tn),
        )
        if cur.fetchone():
            return True, ""
    return False, f"权限不足：当前角色不允许对 {tn} 执行 {action}"


def user_can_read_table(conn, user_id: int, table_name: str) -> bool:
    """供菜单等：当前用户是否对表有读权限（含个人覆盖）。"""
    roles = _get_user_roles(conn, int(user_id))
    ok, _ = _check_table_permission(conn, user_id, roles, table_name, "read")
    return ok


def _audit_r7(
    conn,
    user_id: Optional[int],
    stype: str,
    all_tables: Set[str],
    write_targets: Set[str],
) -> Optional[str]:
    """R7：表级角色权限。user_id 为 None 时跳过（兼容旧调用）。"""
    if user_id is None:
        return None
    roles = _get_user_roles(conn, int(user_id))
    if any(r in _PRIVILEGED_ROLES for r in roles):
        return None
    st = (stype or "").upper()
    if st == "SELECT":
        for t in sorted(all_tables):
            ok, reason = _check_table_permission(conn, user_id, roles, t, "read")
            if not ok:
                return reason
        return None
    if st == "INSERT":
        for t in write_targets:
            ok, reason = _check_table_permission(conn, user_id, roles, t, "write")
            if not ok:
                return reason
        return None
    if st == "UPDATE":
        wt = set(write_targets)
        for t in all_tables:
            act = "write" if t in wt else "read"
            ok, reason = _check_table_permission(conn, user_id, roles, t, act)
            if not ok:
                return reason
        return None
    if st == "DELETE":
        for t in write_targets:
            ok, reason = _check_table_permission(conn, user_id, roles, t, "delete")
            if not ok:
                return reason
        return None
    return None


def _generate_approval_no(cur) -> str:
    cur.execute("SELECT to_char(now() AT TIME ZONE 'UTC', 'YYYY')")
    year = str(cur.fetchone()[0])
    cur.execute(
        "SELECT COUNT(*) FROM business.approvals WHERE approval_no LIKE %s",
        (f"AP-{year}-%",),
    )
    n = (cur.fetchone()[0] or 0) + 1
    return f"AP-{year}-{n:04d}"


def _maybe_create_approval(
    conn,
    user_id: int,
    operation: str,
    table: Optional[str],
    returning: Optional[Dict[str, Any]],
) -> None:
    """INSERT business.contracts / purchase_orders 成功后：置 pending_approval 并写 business.approvals。"""
    if operation != "insert" or not returning:
        return
    tid = returning.get("id")
    if tid is None:
        return
    t = (table or "").strip().lower()
    target_type = _TABLE_TO_APPROVAL_TARGET.get(t)
    if not target_type:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT approver_role FROM meta.approval_rules
            WHERE target_type = %s AND is_active = true
              AND (condition_expr IS NULL OR TRIM(condition_expr) = '')
            LIMIT 1
            """,
            (target_type,),
        )
        row = cur.fetchone()
        if not row:
            return
        approver_role = row[0]
        cur.execute(
            """
            SELECT ua.employee_id FROM business.user_role_grants rg
            JOIN business.user_accounts ua ON ua.id = rg.user_account_id
            WHERE rg.role_code = %s AND ua.is_active = true
              AND ua.employee_id IS NOT NULL
            LIMIT 1
            """,
            (approver_role,),
        )
        r2 = cur.fetchone()
        approver_id = int(r2[0]) if r2 and r2[0] is not None else None

        target_no = None
        if "contract_no" in returning and returning.get("contract_no") is not None:
            target_no = str(returning.get("contract_no"))
        elif "po_no" in returning and returning.get("po_no") is not None:
            target_no = str(returning.get("po_no"))

        approval_no = _generate_approval_no(cur)

        if t == "business.contracts":
            cur.execute(
                "UPDATE business.contracts SET status = 'pending_approval' WHERE id = %s",
                (tid,),
            )
        elif t == "business.purchase_orders":
            cur.execute(
                "UPDATE business.purchase_orders SET status = 'pending_approval' WHERE id = %s",
                (tid,),
            )

        cur.execute(
            """
            INSERT INTO business.approvals
                (approval_no, target_type, target_id, target_no, requested_by, approver_id, status)
            VALUES (%s, %s, %s, %s, %s, %s, 'pending')
            """,
            (approval_no, target_type, int(tid), target_no, user_id, approver_id),
        )


def _get_table_cn(full_table: str, cur) -> Optional[str]:
    """full_table: schema.table（小写）→ meta.meta_tables.table_cn。"""
    parts = full_table.split(".", 1)
    if len(parts) != 2:
        return None
    schema, name = parts[0], parts[1]
    cur.execute(
        """
        SELECT table_cn FROM meta.meta_tables
        WHERE deleted_at IS NULL AND LOWER(table_name) = %s
        LIMIT 1
        """,
        (name.lower(),),
    )
    row = cur.fetchone()
    if row and row[0]:
        return str(row[0]).strip() or None
    return None


def _strip_string_literals_for_scan(sql: str) -> str:
    """将单引号字符串占位，便于检测关键字。"""
    out: List[str] = []
    i = 0
    n = len(sql)
    while i < n:
        c = sql[i]
        if c == "'":
            out.append("''")
            i += 1
            while i < n:
                if sql[i] == "'" and i + 1 < n and sql[i + 1] == "'":
                    i += 2
                    continue
                if sql[i] == "'":
                    i += 1
                    break
                i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _split_statements(sql: str) -> List[str]:
    parts = sqlparse.split(sql)
    return [p.strip() for p in parts if p.strip()]


def _first_statement(sql: str) -> str:
    s = sql.strip()
    if s.endswith(";"):
        s = s[:-1].strip()
    return s


def _strip_explain_prefix(sql: str) -> str:
    s = sql.strip()
    m = re.match(r"^\s*EXPLAIN\s+(?:VERBOSE\s+|ANALYZE\s+)*", s, re.IGNORECASE)
    if m:
        s = s[m.end() :].strip()
    return s


def _get_statement_type(sql: str) -> str:
    """
    简化判定：去掉注释和前导空白后，
    看第一个关键词。WITH 开头的看 CTE 之后的主句动词。
    """
    s = _strip_explain_prefix(sql.strip())
    stripped = s.strip().upper()
    if stripped.startswith("SELECT"):
        return "SELECT"
    if stripped.startswith("INSERT"):
        return "INSERT"
    if stripped.startswith("UPDATE"):
        return "UPDATE"
    if stripped.startswith("DELETE"):
        return "DELETE"
    if stripped.startswith("WITH"):
        depth = 0
        last_keyword = "SELECT"
        tokens = stripped.split()
        for token in tokens:
            depth += token.count("(") - token.count(")")
            if depth == 0 and token in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                last_keyword = token
        return last_keyword
    return "UNKNOWN"


def _add_table_raw(name: str, found: Set[str]) -> None:
    name = name.strip().strip('"')
    if not name or name.upper() in ("SELECT", "WHERE", "ON", "AND", "OR", "AS"):
        return
    # SUBSTRING(x FROM 4) 等误捕获时可能只剩纯数字，不是表名
    if name.isdigit():
        return
    if "." in name:
        a, b = name.split(".", 1)
        found.add(f"{a.lower()}.{b.lower()}")
    else:
        found.add(f"business.{name.lower()}")


def _collect_tables_from_statement(stmt: Statement) -> Set[str]:
    """尽力收集 FROM/JOIN/INTO/UPDATE/DELETE 涉及的表名（小写 schema.table）。"""
    found: Set[str] = set()
    sql = _mask_sql_function_internal_from_clauses(str(stmt))

    for m in re.finditer(
        r"\b(?:FROM|JOIN)\s+([\"]?[\w]+[\"]?(?:\.[\"]?[\w]+[\"]?)?)",
        sql,
        re.IGNORECASE,
    ):
        _add_table_raw(m.group(1), found)

    m = re.search(r"\bINSERT\s+INTO\s+([\"]?[\w]+[\"]?(?:\.[\"]?[\w]+[\"]?)?)", sql, re.IGNORECASE)
    if m:
        _add_table_raw(m.group(1), found)

    m = re.search(r"\bUPDATE\s+([\"]?[\w]+[\"]?(?:\.[\"]?[\w]+[\"]?)?)\s+SET\b", sql, re.IGNORECASE)
    if m:
        _add_table_raw(m.group(1), found)

    m = re.search(r"\bDELETE\s+FROM\s+([\"]?[\w]+[\"]?(?:\.[\"]?[\w]+[\"]?)?)", sql, re.IGNORECASE)
    if m:
        _add_table_raw(m.group(1), found)

    return found


def _collect_write_target_tables(bare: str) -> Set[str]:
    """INSERT INTO / UPDATE … SET / DELETE FROM 的目标表（小写 schema.table）。"""
    found: Set[str] = set()
    for m in re.finditer(
        r"\bINSERT\s+INTO\s+([\"]?[\w]+[\"]?(?:\.[\"]?[\w]+[\"]?)?)",
        bare,
        re.IGNORECASE,
    ):
        _add_table_raw(m.group(1), found)
    for m in re.finditer(
        r"\bUPDATE\s+([\"]?[\w]+[\"]?(?:\.[\"]?[\w]+[\"]?)?)\s+SET\b",
        bare,
        re.IGNORECASE,
    ):
        _add_table_raw(m.group(1), found)
    for m in re.finditer(
        r"\bDELETE\s+FROM\s+([\"]?[\w]+[\"]?(?:\.[\"]?[\w]+[\"]?)?)",
        bare,
        re.IGNORECASE,
    ):
        _add_table_raw(m.group(1), found)
    return found


def _audit_r1_ddl(bare: str) -> Optional[str]:
    upper_flat = bare.upper()
    for bad in _FORBIDDEN_TOP_KEYWORDS:
        if re.search(rf"\b{bad}\b", upper_flat):
            return f"禁止使用的语句类型：{bad}"
    return None


def _audit_sql_confirm_r1_r4(sql: str) -> Dict[str, Any]:
    """
    用户确认执行前防篡改：仅 R1–R4。
    返回 passed, reason, statement_type, target_table。
    """
    if not sql or not isinstance(sql, str):
        return {"passed": False, "reason": "SQL 为空", "statement_type": None, "target_table": None}

    raw = sql.strip()
    parts = _split_statements(raw)
    if len(parts) > 1:
        return {"passed": False, "reason": "一次只能执行一条 SQL，禁止多语句", "statement_type": None, "target_table": None}

    one = _first_statement(raw)
    bare = _strip_string_literals_for_scan(one)

    r1 = _audit_r1_ddl(bare)
    if r1:
        return {"passed": False, "reason": r1, "statement_type": None, "target_table": None}

    stype = _get_statement_type(one)
    if stype not in ("INSERT", "UPDATE", "DELETE"):
        return {
            "passed": False,
            "reason": "确认执行仅支持 INSERT / UPDATE / DELETE",
            "statement_type": stype,
            "target_table": None,
        }

    write_targets = _collect_write_target_tables(bare)
    if not write_targets:
        return {"passed": False, "reason": "未能识别写操作目标表", "statement_type": stype, "target_table": None}

    for t in write_targets:
        sch = t.split(".", 1)[0]
        if sch != "business":
            return {"passed": False, "reason": f"写操作仅允许 business 库表：{t}", "statement_type": stype, "target_table": None}

    target = None
    for t in write_targets:
        if t.startswith("business."):
            target = t
            break
    if not target:
        return {"passed": False, "reason": "未能识别写操作目标表", "statement_type": stype, "target_table": None}

    if stype in ("UPDATE", "DELETE"):
        if not re.search(r"\bWHERE\b", one, re.IGNORECASE):
            return {"passed": False, "reason": f"{stype} 必须包含 WHERE 条件", "statement_type": stype, "target_table": target}

    return {"passed": True, "reason": "", "statement_type": stype, "target_table": target}


def _audit_sql(sql: str, conn, user_id: Optional[int] = None) -> Dict[str, Any]:
    """
    完整审计 R1–R7（preview / execute_sql 用）；R7 需 user_id，为 None 时跳过。
    返回 passed, reason, statement_type, target_table (schema.table 小写)。
    """
    if not sql or not isinstance(sql, str):
        return {"passed": False, "reason": "SQL 为空", "statement_type": None, "target_table": None}

    raw = sql.strip()
    parts = _split_statements(raw)
    if len(parts) > 1:
        return {"passed": False, "reason": "一次只能执行一条 SQL，禁止多语句", "statement_type": None, "target_table": None}

    one = _first_statement(raw)
    bare = _strip_string_literals_for_scan(one)
    upper_flat = bare.upper()

    r1 = _audit_r1_ddl(bare)
    if r1:
        return {"passed": False, "reason": r1, "statement_type": None, "target_table": None}

    parsed = sqlparse.parse(one)
    if not parsed:
        return {"passed": False, "reason": "无法解析 SQL", "statement_type": None, "target_table": None}
    stmt = parsed[0]

    stype = _get_statement_type(one)
    if stype == "UNKNOWN":
        return {
            "passed": False,
            "reason": "仅支持 SELECT / INSERT / UPDATE / DELETE（含 WITH…）",
            "statement_type": None,
            "target_table": None,
        }

    with conn.cursor() as cur:
        whitelist = _load_table_whitelist(cur)

    all_tables = _collect_tables_from_statement(stmt)
    if not all_tables:
        all_tables = _collect_tables_from_statement(sqlparse.parse(one)[0])

    for t in all_tables:
        if t.startswith("pg_catalog.") or t.startswith("information_schema."):
            return {
                "passed": False,
                "reason": "禁止访问 pg_catalog / information_schema",
                "statement_type": stype,
                "target_table": None,
            }
        sch = t.split(".", 1)[0] if "." in t else "business"
        if sch not in ("business", "meta"):
            return {
                "passed": False,
                "reason": f"仅允许访问 business / meta 下的白名单表：{t}",
                "statement_type": stype,
                "target_table": None,
            }
        if t not in whitelist:
            return {"passed": False, "reason": f"表不在领域模型白名单内：{t}", "statement_type": stype, "target_table": None}

    if stype == "SELECT":
        if not all_tables:
            if not re.search(r"\bFROM\b", bare, re.IGNORECASE):
                r7 = _audit_r7(conn, user_id, stype, all_tables, set())
                if r7:
                    return {
                        "passed": False,
                        "reason": r7,
                        "statement_type": stype,
                        "target_table": None,
                    }
                return {"passed": True, "reason": "", "statement_type": stype, "target_table": None}
            return {"passed": False, "reason": "未能识别 SELECT 涉及的表", "statement_type": stype, "target_table": None}
        r7 = _audit_r7(conn, user_id, stype, all_tables, set())
        if r7:
            return {"passed": False, "reason": r7, "statement_type": stype, "target_table": None}
        return {"passed": True, "reason": "", "statement_type": stype, "target_table": None}

    if stype in ("INSERT", "UPDATE", "DELETE"):
        write_targets = _collect_write_target_tables(bare)
        if not write_targets:
            return {"passed": False, "reason": "未能识别写操作目标表", "statement_type": stype, "target_table": None}

        for t in write_targets:
            sch = t.split(".", 1)[0]
            if sch != "business":
                return {"passed": False, "reason": f"写操作仅允许 business 库表：{t}", "statement_type": stype, "target_table": None}
            if t not in whitelist:
                return {"passed": False, "reason": f"表不在领域模型白名单内：{t}", "statement_type": stype, "target_table": None}

        target = None
        for t in write_targets:
            if t.startswith("business."):
                target = t
                break
        if not target:
            return {"passed": False, "reason": "未能识别写操作目标表", "statement_type": stype, "target_table": None}

        if stype in ("UPDATE", "DELETE"):
            if not re.search(r"\bWHERE\b", one, re.IGNORECASE):
                return {"passed": False, "reason": f"{stype} 必须包含 WHERE 条件", "statement_type": stype, "target_table": target}

        r7 = _audit_r7(conn, user_id, stype, all_tables, write_targets)
        if r7:
            return {"passed": False, "reason": r7, "statement_type": stype, "target_table": target}

        return {"passed": True, "reason": "", "statement_type": stype, "target_table": target}

    return {"passed": False, "reason": "未支持的语句类型", "statement_type": stype, "target_table": None}


def _balanced_paren_end(s: str, open_at: int) -> int:
    """s[open_at] 为 '(' 时，返回匹配的 ')' 下标；失败返回 -1。"""
    if open_at >= len(s) or s[open_at] != "(":
        return -1
    depth = 0
    i = open_at
    in_quote = False
    while i < len(s):
        c = s[i]
        if in_quote:
            if c == "'" and i + 1 < len(s) and s[i + 1] == "'":
                i += 2
                continue
            if c == "'":
                in_quote = False
            i += 1
            continue
        if c == "'":
            in_quote = True
            i += 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _mask_sql_function_internal_from_clauses(sql: str) -> str:
    """
    屏蔽 SUBSTRING(x FROM …)、EXTRACT(… FROM …)、OVERLAY(… FROM …)、TRIM(… FROM …)
    等函数括号内的 FROM，避免被 FROM/JOIN 表名正则误识别（如 business.4）。
    """
    pat = re.compile(r"\b(SUBSTRING|OVERLAY|EXTRACT|TRIM)\s*\(", re.IGNORECASE)
    pos = 0
    s = sql
    while True:
        m = pat.search(s, pos)
        if not m:
            return s
        open_idx = m.end() - 1
        if open_idx >= len(s) or s[open_idx] != "(":
            return s
        close_idx = _balanced_paren_end(s, open_idx)
        if close_idx < 0:
            return s
        inner = s[open_idx + 1 : close_idx]
        inner_masked = re.sub(r"\bFROM\b", " __FROM_MASK__ ", inner, flags=re.IGNORECASE)
        if inner_masked == inner:
            pos = close_idx + 1
            continue
        s = s[: open_idx + 1] + inner_masked + s[close_idx:]
        pos = open_idx + 1


def _split_top_level_commas(inner: str) -> List[str]:
    """括号/引号感知的顶层逗号切分。"""
    parts: List[str] = []
    buf: List[str] = []
    depth = 0
    in_quote = False
    i = 0
    n = len(inner)
    while i < n:
        c = inner[i]
        if in_quote:
            buf.append(c)
            if c == "'" and i + 1 < n and inner[i + 1] == "'":
                buf.append(inner[i + 1])
                i += 2
                continue
            if c == "'":
                in_quote = False
            i += 1
            continue
        if c == "'":
            in_quote = True
            buf.append(c)
            i += 1
            continue
        if c == "(":
            depth += 1
            buf.append(c)
            i += 1
            continue
        if c == ")":
            depth = max(0, depth - 1)
            buf.append(c)
            i += 1
            continue
        if c == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    if buf:
        parts.append("".join(buf).strip())
    return parts


def _is_null_token(t: str) -> bool:
    u = t.strip().upper()
    return u == "NULL" or u == "DEFAULT"


def _insert_columns_if_present(sql: str) -> Optional[List[str]]:
    """INSERT INTO t (a, b) VALUES → 列名列表。"""
    m = re.search(
        r"\bINSERT\s+INTO\s+[\"]?[\w]+[\"]?(?:\.[\"]?[\w]+[\"]?)?\s*\(",
        sql,
        re.IGNORECASE,
    )
    if not m:
        return None
    open_at = m.end() - 1
    end = _balanced_paren_end(sql, open_at)
    if end < 0:
        return None
    inner = sql[open_at + 1 : end]
    cols = _split_top_level_commas(inner)
    out = [c.strip().strip('"') for c in cols if c.strip()]
    return out if out else None


_UA_TABLE = "business.user_accounts"
_BCRYPT_PREFIX_RE = re.compile(r"^\$2[aby]\$\d{2}\$")


def _looks_like_bcrypt_hash(value: Any) -> bool:
    if value is None:
        return False
    s = str(value).strip()
    return bool(_BCRYPT_PREFIX_RE.match(s))


def _default_password_hash_bcrypt() -> str:
    """新建账号默认口令的哈希；用户首次登录后应修改（业务侧改密后续再做）。"""
    return bcrypt.hashpw(b"123456", bcrypt.gensalt()).decode()


def _replace_first_values_inner(sql: str, new_inner: str) -> str:
    m = re.search(r"\bVALUES\s*\(", sql, re.IGNORECASE)
    if not m:
        return sql
    open_at = m.start() + len(m.group(0)) - 1
    end = _balanced_paren_end(sql, open_at)
    if end < 0:
        return sql
    return sql[: open_at + 1] + new_inner + sql[end:]


def _normalize_ua_insert_password(sql: str, params: List[Any]) -> Tuple[str, List[Any]]:
    cols = _insert_columns_if_present(sql)
    if not cols:
        return sql, params
    ph_idx = None
    for i, c in enumerate(cols):
        if c.lower() == "password_hash":
            ph_idx = i
            break
    if ph_idx is None:
        return sql, params
    inner = _first_values_tuple_inner(sql)
    if inner is None:
        return sql, params
    vals = _split_top_level_commas(inner)
    if ph_idx >= len(vals):
        return sql, params
    new_params = list(params)
    tok = vals[ph_idx].strip()
    if tok == "%s":
        pi = 0
        for i, t in enumerate(vals):
            if t.strip() != "%s":
                continue
            if i == ph_idx:
                if pi < len(new_params) and not _looks_like_bcrypt_hash(new_params[pi]):
                    new_params[pi] = _default_password_hash_bcrypt()
                break
            pi += 1
        return sql, new_params
    if _is_null_token(tok):
        return sql, params
    m = re.match(r"^'(.*)'$", tok, re.DOTALL)
    if not m:
        return sql, params
    inner_val = m.group(1).replace("''", "'")
    if _looks_like_bcrypt_hash(inner_val):
        return sql, params
    nh = _default_password_hash_bcrypt()
    new_vals = list(vals)
    new_vals[ph_idx] = "'" + nh.replace("'", "''") + "'"
    new_inner = ",".join(new_vals)
    return _replace_first_values_inner(sql, new_inner), new_params


def _normalize_ua_update_password(sql: str, params: List[Any]) -> Tuple[str, List[Any]]:
    new_params = list(params)

    def repl_quoted(m):
        qval = m.group(2)
        literal = qval.replace("''", "'")
        if _looks_like_bcrypt_hash(literal):
            return m.group(0)
        nh = _default_password_hash_bcrypt()
        return m.group(1) + "'" + nh.replace("'", "''") + "'"

    new_sql = re.sub(
        r"(?i)(\bpassword_hash\s*=\s*)'([^']*)'",
        repl_quoted,
        sql,
        count=1,
    )
    m = re.search(r"(?i)\bpassword_hash\s*=\s*%s", new_sql)
    if m:
        pidx = new_sql[: m.end()].count("%s") - 1
        if 0 <= pidx < len(new_params) and not _looks_like_bcrypt_hash(new_params[pidx]):
            new_params[pidx] = _default_password_hash_bcrypt()
    return new_sql, new_params


def _normalize_user_accounts_password(sql: str, params: List[Any]) -> Tuple[str, List[Any]]:
    """
    对 business.user_accounts 的 INSERT/UPDATE：若 password_hash 非 bcrypt（$2a$/$2b$/$2y$），
    替换为默认口令 123456 的哈希。
    """
    raw = (sql or "").strip()
    if not raw:
        return raw, list(params or [])
    params = list(params or [])
    one = _first_statement(raw)
    bare = _strip_string_literals_for_scan(one)
    targets = _collect_write_target_tables(bare)
    if not any(t.lower() == _UA_TABLE for t in targets):
        return raw, params
    st = _get_statement_type(one)
    if st == "INSERT":
        return _normalize_ua_insert_password(raw, params)
    if st == "UPDATE":
        return _normalize_ua_update_password(raw, params)
    return raw, params


def _first_values_tuple_inner(sql: str) -> Optional[str]:
    """VALUES ( ... ) 中第一组值的内部字符串。"""
    m = re.search(r"\bVALUES\s*\(", sql, re.IGNORECASE)
    if not m:
        return None
    open_at = m.start() + len(m.group(0)) - 1
    end = _balanced_paren_end(sql, open_at)
    if end < 0:
        return None
    return sql[open_at + 1 : end]


def _detail_insert_values(sql: str) -> Optional[str]:
    """INSERT：取前几个非 NULL 字段的 col=value 或裸值片段。"""
    try:
        inner = _first_values_tuple_inner(sql)
        if not inner:
            return None
        vals = _split_top_level_commas(inner)
        cols = _insert_columns_if_present(sql)
        pairs: List[str] = []
        for i, v in enumerate(vals[:8]):
            if _is_null_token(v):
                continue
            if cols and i < len(cols):
                pairs.append(f"{cols[i]}={v.strip()}")
            else:
                pairs.append(v.strip())
            if len(pairs) >= 4:
                break
        if not pairs:
            return None
        return "(" + ", ".join(pairs) + ")"
    except Exception:
        return None


def _detail_update_set_where(sql: str) -> Optional[str]:
    """UPDATE：SET 与 WHERE 片段。"""
    try:
        sm = re.search(r"\bSET\b", sql, re.IGNORECASE)
        wm = re.search(r"\bWHERE\b", sql, re.IGNORECASE)
        if not sm or not wm or wm.start() <= sm.end():
            return None
        set_part = sql[sm.end() : wm.start()].strip()
        where_part = sql[wm.end() :].strip()
        if len(set_part) > 400:
            set_part = set_part[:400] + "…"
        if len(where_part) > 300:
            where_part = where_part[:300] + "…"
        return f"SET {set_part} WHERE {where_part}"
    except Exception:
        return None


def _detail_delete_where(sql: str) -> Optional[str]:
    try:
        wm = re.search(r"\bWHERE\b", sql, re.IGNORECASE)
        if not wm:
            return None
        where_part = sql[wm.end() :].strip()
        if len(where_part) > 400:
            where_part = where_part[:400] + "…"
        return f"WHERE {where_part}"
    except Exception:
        return None


def _generate_summary(sql: str, params: Optional[list], audit: Dict[str, Any], conn) -> str:
    op = audit.get("statement_type") or ""
    table = audit.get("target_table") or ""
    label = table or "表"
    if table:
        with conn.cursor() as cur:
            tc = _get_table_cn(table, cur)
        if tc:
            label = tc
    base_insert = f"将在【{label}】中新建一条记录"
    base_update = f"将修改【{label}】中的记录"
    base_delete = f"将删除【{label}】中的记录"

    if op == "INSERT":
        extra = _detail_insert_values(sql)
        if extra:
            return f"{base_insert}{extra}"
        return base_insert
    if op == "UPDATE":
        extra = _detail_update_set_where(sql)
        if extra:
            return f"{base_update}（{extra}）"
        return base_update
    if op == "DELETE":
        extra = _detail_delete_where(sql)
        if extra:
            return f"{base_delete}（{extra}）"
        return base_delete
    return f"将对【{label}】执行 {op} 操作"


def execute_sql(
    sql: str,
    params: Optional[list] = None,
    conn=None,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    审计并执行 SELECT（仅 SELECT；EXPLAIN SELECT 按 SELECT 审计并执行）。
    """
    params = list(params or [])

    def _run(c) -> Dict[str, Any]:
        audit = _audit_sql(sql, c, user_id)
        if not audit["passed"]:
            reason = audit["reason"]
            logger.warning("execute_sql: 审计未通过: %s | sql=%r", reason, sql[:500])
            if not is_audit_permission_only(reason):
                log_incident(
                    stage="audit",
                    error_message=reason,
                    user_id=user_id,
                    generated_sql=sql,
                    context={"fn": "execute_sql"},
                )
            return {"success": False, "error": reason}
        st = audit["statement_type"]
        if st != "SELECT":
            return {"success": False, "error": "execute_sql 只执行 SELECT；写操作请用 preview_sql"}

        to_run = sql.strip()

        try:
            cur = c.cursor(cursor_factory=RealDictCursor)
            _cursor_execute(cur, to_run, params)
            rows = cur.fetchall() or []
            cols = [d[0] for d in cur.description] if cur.description else []
            return {
                "success": True,
                "rows": [dict(r) for r in rows],
                "count": len(rows),
                "columns": cols,
            }
        except Exception as e:
            logger.warning("execute_sql: 数据库执行异常: %s | sql=%r", e, sql[:500])
            log_incident(
                stage="execute",
                error_message=str(e),
                user_id=user_id,
                generated_sql=sql,
                context={"fn": "execute_sql"},
            )
            return {"success": False, "error": str(e)}

    if conn is not None:
        return _run(conn)
    with db_conn() as c:
        return _run(c)


def preview_sql(
    sql: str,
    params: Optional[list] = None,
    conn=None,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """审计写 SQL，不执行，生成摘要。"""
    params = list(params or [])

    def _run(c) -> Dict[str, Any]:
        audit = _audit_sql(sql, c, user_id)
        if not audit["passed"]:
            reason = audit["reason"]
            logger.warning("preview_sql: 审计未通过: %s | sql=%r", reason, sql[:500])
            if not is_audit_permission_only(reason):
                log_incident(
                    stage="audit",
                    error_message=reason,
                    user_id=user_id,
                    generated_sql=sql,
                    context={"fn": "preview_sql"},
                )
            return {"success": False, "error": reason}
        st = audit["statement_type"]
        if st == "SELECT":
            return {"success": False, "error": "SELECT 请用 execute_sql"}
        if st not in ("INSERT", "UPDATE", "DELETE"):
            return {"success": False, "error": "preview_sql 仅支持 INSERT/UPDATE/DELETE"}

        sql, params = _normalize_user_accounts_password(sql.strip(), params)
        summary = _generate_summary(sql, params, audit, c)
        sql_clean = sql.strip()
        hw = highlight_words_from_summary(summary)
        return {
            "success": True,
            "summary": summary,
            "highlight_words": hw,
            "sql": sql_clean,
            "params": params,
            "payload": {"sql": sql_clean, "params": params},
            "operation": st.lower(),
            "table": audit.get("target_table") or "",
            "stage": "preview",
        }

    if conn is not None:
        return _run(conn)
    with db_conn() as c:
        return _run(c)


def _confirm_write_single(
    conn, sql: str, params: List[Any], user_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    审计并执行一条写 SQL，不 commit / rollback（由调用方在事务末统一处理）。
    确认前完整审计 R1–R7（与 preview 一致）；INSERT 合同/采购订单后可能写审批单。
    """
    sql = (sql or "").strip()
    params = list(params or [])
    audit = _audit_sql(sql, conn, user_id)
    if not audit["passed"]:
        reason = audit["reason"]
        logger.warning("confirm_execute_sql: 审计未通过: %s | sql=%r", reason, sql[:500])
        if not is_audit_permission_only(reason):
            log_incident(
                stage="audit",
                error_message=reason,
                user_id=user_id,
                generated_sql=sql,
                context={"fn": "_confirm_write_single"},
            )
        return {"success": False, "error": reason}
    st = audit["statement_type"]
    if st == "SELECT":
        return {"success": False, "error": "confirm 不执行 SELECT"}
    if st not in ("INSERT", "UPDATE", "DELETE"):
        return {"success": False, "error": "仅支持 INSERT/UPDATE/DELETE"}

    sql, params = _normalize_user_accounts_password(sql, params)

    cur = None
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        _cursor_execute(cur, sql.strip(), params)
        affected = cur.rowcount
        returning_row = None
        if cur.description:
            row = cur.fetchone()
            if row is not None:
                returning_row = dict(row)
        rid = None
        if returning_row and "id" in returning_row:
            rid = returning_row["id"]
        if user_id is not None:
            _maybe_create_approval(
                conn,
                int(user_id),
                st.lower(),
                audit.get("target_table"),
                returning_row,
            )
        return {
            "success": True,
            "affected_rows": affected,
            "returning": returning_row,
            "operation": st.lower(),
            "table": audit.get("target_table"),
            "id": rid,
        }
    except Exception as e:
        log_incident(
            stage="execute",
            error_message=str(e),
            user_id=user_id,
            generated_sql=sql,
            context={"fn": "_confirm_write_single"},
        )
        return translate_db_write_exception(e, cur)


def confirm_batch_sql(
    items: List[Dict[str, Any]], conn, user_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    顺序执行多条写 SQL（均已通过 preview），同一连接内一次 commit。
    items: [{"sql": str, "params": []}, ...]
    """
    results: List[Dict[str, Any]] = []
    for i, item in enumerate(items):
        sql = (item.get("sql") or "").strip()
        params = list(item.get("params") or [])
        if not sql:
            conn.rollback()
            return {
                "success": False,
                "error": f"batch[{i}] 缺少 sql",
                "results": results,
                "failed_index": i,
            }
        r = _confirm_write_single(conn, sql, params, user_id)
        if not r.get("success"):
            conn.rollback()
            return {**r, "results": results, "failed_index": i}
        results.append(r)
    try:
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.exception("confirm_batch_sql commit: %s", e)
        log_incident(
            stage="execute",
            error_message=str(e),
            user_id=user_id,
            context={"fn": "confirm_batch_sql", "phase": "commit"},
        )
        with conn.cursor() as c_commit:
            tr = translate_db_write_exception(e, c_commit)
        return {**tr, "results": results}
    return {"success": True, "results": results, "count": len(results)}


def verify_confirmed_writes(
    batch: List[Dict[str, Any]],
    confirm_result: Dict[str, Any],
) -> Dict[str, Any]:
    """
    用户确认并执行成功后，按预览条数与执行结果做一致性校验（非业务断言，仅执行层）。
    - 条数与每条 success 一致
    - INSERT 影响行数应 >= 1（若引擎返回 affected_rows）
    """
    if not confirm_result.get("success"):
        return {"ok": False, "reason": confirm_result.get("error") or "执行未成功"}
    results = confirm_result.get("results")
    if not isinstance(results, list):
        return {"ok": False, "reason": "执行结果缺少 results 列表"}
    if len(results) != len(batch):
        return {
            "ok": False,
            "reason": (
                f"预览 {len(batch)} 条与执行结果 {len(results)} 条不一致"
            ),
        }
    for i, (item, r) in enumerate(zip(batch, results)):
        if not r.get("success"):
            return {
                "ok": False,
                "reason": f"第 {i + 1} 条：{r.get('error', '失败')}",
            }
        op = (r.get("operation") or item.get("operation") or "").lower()
        if op == "insert":
            ar = r.get("affected_rows")
            if ar is not None and ar < 1:
                return {
                    "ok": False,
                    "reason": f"第 {i + 1} 条 INSERT 影响行数为 {ar}，预期至少 1",
                }
    return {"ok": True, "reason": ""}


def confirm_execute_sql(
    sql: str,
    params: Optional[list] = None,
    conn=None,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    用户确认后执行写 SQL：再次审计（R1–R4）并执行，INSERT 建议带 RETURNING id。
    """
    params = list(params or [])

    def _run(c) -> Dict[str, Any]:
        r = _confirm_write_single(c, sql, params, user_id)
        if r.get("success"):
            try:
                c.commit()
            except Exception as e:
                c.rollback()
                return {"success": False, "error": str(e)}
        else:
            c.rollback()
        return r

    if conn is not None:
        return _run(conn)
    with db_conn() as c:
        return _run(c)
