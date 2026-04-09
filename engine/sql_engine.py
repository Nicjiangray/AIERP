"""
统一 SQL 引擎：安全审计后执行 SELECT，或对写 SQL 做预览摘要。
替代面向结构化 payload 的 query/write 执行路径（query_engine / write_engine 仍保留为 deprecated）。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set

import sqlparse
from sqlparse.sql import Statement

from psycopg2.extras import RealDictCursor

from tools.database import db_conn

# 写操作禁止出现在 SET 中的列（UPDATE）
_FORBIDDEN_WRITE_COLS = frozenset({"id", "created_at", "updated_at"})

_MAX_SELECT_LIMIT = 100

_FORBIDDEN_TOP_KEYWORDS = frozenset(
    {
        "DROP",
        "ALTER",
        "TRUNCATE",
        "CREATE",
        "GRANT",
        "REVOKE",
        "COPY",
    }
)


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
    """将单引号字符串占位，便于检测注释/关键字。"""
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


def _has_blocked_comments(sql: str) -> bool:
    bare = _strip_string_literals_for_scan(sql)
    if "--" in bare:
        return True
    if "/*" in bare or "*/" in bare:
        return True
    return False


def _split_statements(sql: str) -> List[str]:
    parts = sqlparse.split(sql)
    return [p.strip() for p in parts if p.strip()]


def _first_statement(sql: str) -> str:
    s = sql.strip()
    if s.endswith(";"):
        s = s[:-1].strip()
    return s


def _collect_tables_from_statement(stmt: Statement) -> Set[str]:
    """尽力收集 FROM/JOIN/INTO/UPDATE/DELETE 涉及的表名（小写 schema.table）。"""
    found: Set[str] = set()
    sql = str(stmt)
    upper = sql.upper()

    def add_raw(name: str) -> None:
        name = name.strip().strip('"')
        if not name or name.upper() in ("SELECT", "WHERE", "ON", "AND", "OR", "AS"):
            return
        if "." in name:
            a, b = name.split(".", 1)
            found.add(f"{a.lower()}.{b.lower()}")
        else:
            # 无 schema 时默认 business（与常见业务表一致）
            found.add(f"business.{name.lower()}")

    # FROM / JOIN
    for m in re.finditer(
        r"\b(?:FROM|JOIN)\s+([\"]?[\w]+[\"]?(?:\.[\"]?[\w]+[\"]?)?)",
        sql,
        re.IGNORECASE,
    ):
        add_raw(m.group(1))

    # INSERT INTO x
    m = re.search(r"\bINSERT\s+INTO\s+([\"]?[\w]+[\"]?(?:\.[\"]?[\w]+[\"]?)?)", sql, re.IGNORECASE)
    if m:
        add_raw(m.group(1))

    # UPDATE x SET
    m = re.search(r"\bUPDATE\s+([\"]?[\w]+[\"]?(?:\.[\"]?[\w]+[\"]?)?)\s+SET\b", sql, re.IGNORECASE)
    if m:
        add_raw(m.group(1))

    # DELETE FROM x
    m = re.search(r"\bDELETE\s+FROM\s+([\"]?[\w]+[\"]?(?:\.[\"]?[\w]+[\"]?)?)", sql, re.IGNORECASE)
    if m:
        add_raw(m.group(1))

    return found


def _nested_write_forbidden(sql: str) -> bool:
    """括号内出现 INSERT/UPDATE/DELETE 作为子语句则拒绝（字符串已剥离后更安全）。"""
    bare = _strip_string_literals_for_scan(sql)
    depth = 0
    i = 0
    n = len(bare)
    while i < n:
        c = bare[i]
        if c == "(":
            depth += 1
            i += 1
            continue
        if c == ")":
            depth = max(0, depth - 1)
            i += 1
            continue
        if depth > 0:
            rest = bare[i:].upper()
            for word in ("INSERT", "UPDATE", "DELETE"):
                if rest.startswith(word):
                    j = i + len(word)
                    if j >= n or not (bare[j].isalnum() or bare[j] == "_"):
                        return True
        i += 1
    return False


def _has_limit_clause(sql: str) -> bool:
    return bool(re.search(r"\bLIMIT\s+\d+", sql, re.IGNORECASE))


def _is_explain_select(sql: str) -> bool:
    u = sql.lstrip()
    if not u.upper().startswith("EXPLAIN"):
        return False
    rest = u[len("EXPLAIN") :].lstrip()
    return rest.upper().startswith("SELECT")


def _update_set_columns(sql: str) -> List[str]:
    """粗解析 UPDATE ... SET 后的赋值列名（小写）。"""
    m = re.search(r"\bSET\s+(.*?)\s+WHERE\b", sql, re.IGNORECASE | re.DOTALL)
    if not m:
        return []
    chunk = m.group(1)
    cols: List[str] = []
    for part in chunk.split(","):
        part = part.strip()
        if not part:
            continue
        eq = part.find("=")
        if eq <= 0:
            continue
        left = part[:eq].strip()
        left = left.split()[-1] if left else ""
        left = left.strip('"').split(".")[-1].lower()
        if left:
            cols.append(left)
    return cols


def _audit_sql(sql: str, conn) -> Dict[str, Any]:
    """
    返回:
      passed, reason, statement_type, target_table (schema.table 小写)
    """
    if not sql or not isinstance(sql, str):
        return {"passed": False, "reason": "SQL 为空", "statement_type": None, "target_table": None}

    raw = sql.strip()
    if _has_blocked_comments(raw):
        return {"passed": False, "reason": "禁止在 SQL 中使用注释（-- 或 /* */）", "statement_type": None, "target_table": None}

    parts = _split_statements(raw)
    if len(parts) > 1:
        return {"passed": False, "reason": "一次只能执行一条 SQL，禁止多语句", "statement_type": None, "target_table": None}

    one = _first_statement(raw)
    bare = _strip_string_literals_for_scan(one)
    upper_flat = bare.upper()

    for bad in _FORBIDDEN_TOP_KEYWORDS:
        if re.search(rf"\b{bad}\b", upper_flat):
            return {"passed": False, "reason": f"禁止使用的语句类型：{bad}", "statement_type": None, "target_table": None}

    if re.search(r"\bEXPLAIN\b", upper_flat):
        if not _is_explain_select(one):
            return {"passed": False, "reason": "仅允许 EXPLAIN SELECT", "statement_type": None, "target_table": None}

    if _nested_write_forbidden(one):
        return {"passed": False, "reason": "禁止在子查询中包含写操作（INSERT/UPDATE/DELETE）", "statement_type": None, "target_table": None}

    parsed = sqlparse.parse(one)
    if not parsed:
        return {"passed": False, "reason": "无法解析 SQL", "statement_type": None, "target_table": None}
    stmt = parsed[0]

    if _is_explain_select(one):
        stype = "SELECT"
    elif re.match(r"^\s*WITH\b", one, re.IGNORECASE):
        stype = "SELECT"
    else:
        m = re.match(r"^\s*(SELECT|INSERT|UPDATE|DELETE)\b", one, re.IGNORECASE)
        if not m:
            return {"passed": False, "reason": "只允许 SELECT / INSERT / UPDATE / DELETE（或 WITH…SELECT）", "statement_type": None, "target_table": None}
        stype = m.group(1).upper()

    with conn.cursor() as cur:
        whitelist = _load_table_whitelist(cur)

    tables = _collect_tables_from_statement(stmt)
    if not tables:
        tables = _collect_tables_from_statement(sqlparse.parse(one)[0])

    # 禁止访问系统 schema
    for t in tables:
        if t.startswith("pg_catalog.") or t.startswith("information_schema."):
            return {
                "passed": False,
                "reason": "禁止访问 pg_catalog / information_schema",
                "statement_type": stype,
                "target_table": None,
            }

    if stype == "SELECT":
        if not tables:
            return {"passed": False, "reason": "未能识别 SELECT 涉及的表", "statement_type": stype, "target_table": None}
        for t in tables:
            sch = t.split(".", 1)[0]
            if sch not in ("business", "meta"):
                return {"passed": False, "reason": f"SELECT 仅允许访问 business / meta 下的白名单表：{t}", "statement_type": stype, "target_table": None}
            if t not in whitelist:
                return {"passed": False, "reason": f"表不在领域模型白名单内：{t}", "statement_type": stype, "target_table": None}
        return {"passed": True, "reason": "", "statement_type": stype, "target_table": None}

    # 写操作
    if stype in ("INSERT", "UPDATE", "DELETE"):
        if not tables:
            return {"passed": False, "reason": "未能识别写操作目标表", "statement_type": stype, "target_table": None}
        for t in tables:
            sch = t.split(".", 1)[0]
            if sch != "business":
                return {"passed": False, "reason": f"写操作仅允许 business 库表：{t}", "statement_type": stype, "target_table": None}
            if t not in whitelist:
                return {"passed": False, "reason": f"表不在领域模型白名单内：{t}", "statement_type": stype, "target_table": None}

        # 目标表：取第一个 business 表
        target = None
        for t in tables:
            if t.startswith("business."):
                target = t
                break
        if not target:
            return {"passed": False, "reason": "未能识别写操作目标表", "statement_type": stype, "target_table": None}

        if stype in ("UPDATE", "DELETE"):
            if not re.search(r"\bWHERE\b", one, re.IGNORECASE):
                return {"passed": False, "reason": f"{stype} 必须包含 WHERE 条件", "statement_type": stype, "target_table": target}
            # 禁止无条件删除：WHERE 1=1 仍算有条件；WHERE 恒真由 DBA 控制
            wm = re.search(r"\bWHERE\s+(.+?)(?:ORDER BY|LIMIT|$)", one, re.IGNORECASE | re.DOTALL)
            if wm:
                wclause = wm.group(1).strip()
                if re.match(r"^\s*TRUE\s*$", wclause, re.IGNORECASE):
                    return {"passed": False, "reason": "DELETE/UPDATE 的 WHERE 不能仅为 TRUE", "statement_type": stype, "target_table": target}

        if stype == "UPDATE":
            for c in _update_set_columns(one):
                if c in _FORBIDDEN_WRITE_COLS:
                    return {
                        "passed": False,
                        "reason": f"禁止在 UPDATE 中修改系统字段：{c}",
                        "statement_type": stype,
                        "target_table": target,
                    }

        return {"passed": True, "reason": "", "statement_type": stype, "target_table": target}

    return {"passed": False, "reason": "未支持的语句类型", "statement_type": stype, "target_table": None}


def _append_limit_if_needed(sql: str) -> str:
    one = _first_statement(sql)
    if _has_limit_clause(one):
        return sql.strip()
    if one.rstrip().endswith(";"):
        return one[:-1].rstrip() + f" LIMIT {_MAX_SELECT_LIMIT};"
    return one.rstrip() + f" LIMIT {_MAX_SELECT_LIMIT}"


def _generate_summary(sql: str, params: Optional[list], audit: Dict[str, Any], conn) -> str:
    op = audit.get("statement_type") or ""
    table = audit.get("target_table") or ""
    label = table or "表"
    if table:
        with conn.cursor() as cur:
            tc = _get_table_cn(table, cur)
        if tc:
            label = tc
    if op == "INSERT":
        return f"将在【{label}】中新建一条记录"
    if op == "UPDATE":
        return f"将修改【{label}】中的记录"
    if op == "DELETE":
        return f"将删除【{label}】中的记录"
    return f"将对【{label}】执行 {op} 操作"


def execute_sql(
    sql: str,
    params: Optional[list] = None,
    conn=None,
) -> Dict[str, Any]:
    """
    审计并执行 SELECT（仅 SELECT；EXPLAIN SELECT 按 SELECT 审计并执行）。
    """
    params = list(params or [])

    def _run(c) -> Dict[str, Any]:
        audit = _audit_sql(sql, c)
        if not audit["passed"]:
            return {"success": False, "error": audit["reason"]}
        st = audit["statement_type"]
        if st != "SELECT":
            return {"success": False, "error": "execute_sql 只执行 SELECT；写操作请用 preview_sql"}

        to_run = sql.strip()
        if not to_run.upper().lstrip().startswith("EXPLAIN"):
            to_run = _append_limit_if_needed(to_run)

        try:
            cur = c.cursor(cursor_factory=RealDictCursor)
            cur.execute(to_run, params)
            rows = cur.fetchall() or []
            cols = [d[0] for d in cur.description] if cur.description else []
            return {
                "success": True,
                "rows": [dict(r) for r in rows],
                "count": len(rows),
                "columns": cols,
            }
        except Exception as e:
            return {"success": False, "error": f"SQL执行失败: {e}"}

    if conn is not None:
        return _run(conn)
    with db_conn() as c:
        return _run(c)


def preview_sql(
    sql: str,
    params: Optional[list] = None,
    conn=None,
) -> Dict[str, Any]:
    """审计写 SQL，不执行，生成摘要。"""
    params = list(params or [])

    def _run(c) -> Dict[str, Any]:
        audit = _audit_sql(sql, c)
        if not audit["passed"]:
            return {"success": False, "error": audit["reason"]}
        st = audit["statement_type"]
        if st == "SELECT":
            return {"success": False, "error": "SELECT 请用 execute_sql"}
        if st not in ("INSERT", "UPDATE", "DELETE"):
            return {"success": False, "error": "preview_sql 仅支持 INSERT/UPDATE/DELETE"}

        summary = _generate_summary(sql, params, audit, c)
        sql_clean = sql.strip()
        return {
            "success": True,
            "summary": summary,
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


def confirm_execute_sql(sql: str, params: Optional[list] = None, conn=None) -> Dict[str, Any]:
    """
    用户确认后执行写 SQL：再次审计并执行，INSERT 建议带 RETURNING id。
    """
    params = list(params or [])

    def _run(c) -> Dict[str, Any]:
        audit = _audit_sql(sql, c)
        if not audit["passed"]:
            return {"success": False, "error": audit["reason"]}
        st = audit["statement_type"]
        if st == "SELECT":
            return {"success": False, "error": "confirm 不执行 SELECT"}
        if st not in ("INSERT", "UPDATE", "DELETE"):
            return {"success": False, "error": "仅支持 INSERT/UPDATE/DELETE"}

        try:
            cur = c.cursor(cursor_factory=RealDictCursor)
            cur.execute(sql.strip(), params)
            affected = cur.rowcount
            returning_row = None
            if cur.description:
                row = cur.fetchone()
                if row is not None:
                    returning_row = dict(row)
            c.commit()
            rid = None
            if returning_row and "id" in returning_row:
                rid = returning_row["id"]
            return {
                "success": True,
                "affected_rows": affected,
                "returning": returning_row,
                "operation": st.lower(),
                "table": audit.get("target_table"),
                "id": rid,
            }
        except Exception as e:
            c.rollback()
            return {"success": False, "error": f"执行失败: {e}"}

    if conn is not None:
        return _run(conn)
    with db_conn() as c:
        return _run(c)
