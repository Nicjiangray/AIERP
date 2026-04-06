"""
从 PostgreSQL `business.workflow_steps_legacy` 按 `step_order` 读取步骤并执行，
每步结果写入 context，供后续步骤使用。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional

import psycopg2.extras

from llm.client import chat
from tools import TOOLS
from tools.database import db_conn_llm

logger = logging.getLogger(__name__)

_PLACEHOLDER = re.compile(r"\{([^}]+)\}")


class WorkflowStepError(RuntimeError):
    """某一步执行失败且策略为终止时抛出，附带步骤信息。"""

    def __init__(
        self,
        message: str,
        *,
        step_order: Optional[int] = None,
        step_name: Optional[str] = None,
        step_type: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.step_order = step_order
        self.step_name = step_name
        self.step_type = step_type


def _single_sql(sql: str) -> str:
    s = sql.strip().rstrip(";").strip()
    if ";" in s:
        raise ValueError("query_sql 步骤仅允许单条 SQL。")
    return s


def _substitute_template(tpl: str, ctx: Dict[str, Any], *, label: str) -> str:
    """将模板中的 {key} 替换为 context[key] 的字符串形式；SQL 片段内单引号加倍。"""

    def repl(m: Any) -> str:
        key = m.group(1).strip()
        if key not in ctx:
            raise KeyError(f"{label}: 占位符 {{{key}}} 在 context 中无对应值")
        v = ctx[key]
        if v is None:
            return ""
        return str(v).replace("'", "''")

    return _PLACEHOLDER.sub(repl, tpl)


def _run_query_sql(ctx: Dict[str, Any], sql_template: str, output_key: Optional[str]) -> None:
    stmt = _substitute_template(sql_template.strip(), ctx, label="query_sql")
    validated = _single_sql(stmt)
    with db_conn_llm() as conn:
        head = validated.lstrip()
        first = re.split(r"\s+", head, maxsplit=1)[0].upper()
        if first == "SELECT":
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(validated)
                rows = cur.fetchall()
            plain = [dict(r) for r in rows]
            if output_key:
                if len(plain) == 0:
                    ctx[output_key] = None
                elif len(plain) == 1:
                    ctx[output_key] = plain[0]
                else:
                    ctx[output_key] = plain
        else:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(validated)
                n = cur.rowcount
            if output_key:
                ctx[output_key] = n


def _run_code_step(ctx: Dict[str, Any], action: str, output_key: Optional[str]) -> None:
    name = (action or "").strip()
    fn = TOOLS.get(name)
    if fn is None:
        raise RuntimeError(f"code 步骤未注册 action: {name!r}")
    out = fn(dict(ctx))
    if isinstance(out, dict):
        ctx.update(out)
        if output_key:
            ctx[output_key] = out.get(output_key, out)
    else:
        if output_key:
            ctx[output_key] = out


def _run_llm_step(ctx: Dict[str, Any], prompt_template: str, output_key: Optional[str]) -> None:
    pt = (prompt_template or "").strip()
    if not pt:
        raise RuntimeError("llm 步骤 prompt_template 为空")
    try:
        blob = json.dumps(ctx, ensure_ascii=False, default=str)
    except TypeError as e:
        raise RuntimeError(f"context 无法序列化为 JSON：{e}") from e
    messages = [
        {"role": "system", "content": pt},
        {"role": "user", "content": f"当前 context（JSON）：\n{blob}"},
    ]
    text = chat(messages)
    if output_key:
        ctx[output_key] = text


def _fetch_steps(cur: Any, intent: str) -> List[Dict[str, Any]]:
    cur.execute(
        """
        SELECT
            step_order,
            step_name,
            step_type,
            action,
            sql_template,
            prompt_template,
            input_keys,
            output_key,
            on_fail
        FROM business.workflow_steps_legacy
        WHERE workflow_intent = %s
        ORDER BY step_order ASC
        """,
        (intent,),
    )
    return [dict(r) for r in cur.fetchall()]


def _ensure_workflow_exists(cur: Any, intent: str) -> bool:
    cur.execute(
        """
        SELECT 1 FROM business.workflows
        WHERE intent = %s AND COALESCE(is_active, 1) = 1
        LIMIT 1
        """,
        (intent,),
    )
    return cur.fetchone() is not None


def _normalize_on_fail(raw: Any) -> str:
    s = (raw if raw is not None else "stop").strip().lower()
    if s in ("stop", "skip", "ask_user"):
        return s
    return "stop"


def run_workflow(
    intent: str,
    context: Dict[str, Any],
    *,
    on_step_complete: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """
    按 intent 加载 ``business.workflow_steps_legacy``（旧版预置 SQL 步骤），逐步执行并将结果写入 *context*。

    * ``query_sql``：替换 ``sql_template`` 中的 ``{key}`` 后执行；``SELECT`` 结果写入
      ``output_key``（0 行 ``None``，1 行字典，多行列表）；其它语句写入受影响行数。
    * ``code``：调用 ``TOOLS[action](context)``；若返回 dict 则合并进 context，并令
      ``context[output_key] = 返回值[output_key]``（缺省键时则为整份返回值）；非 dict 则直接
      写入 ``output_key``。
    * ``llm``：将 ``prompt_template`` 与当前 context 拼成对话后调用 LLM，文本写入 ``output_key``。

    *on_fail*：``stop`` 抛 ``WorkflowStepError``；``skip`` 记日志并继续；``ask_user`` 返回
    带 ``workflow_awaiting_user``、``workflow_user_message`` 的 context。

    *on_step_complete*：若提供，则在每一步成功结束、``on_fail=skip`` 继续前、或 ``ask_user``
    返回前调用一次，参数为当前 context 的浅拷贝（便于测试打印或断言）。
    """
    ctx = dict(context)
    ctx["workflow_intent"] = intent

    try:
        with db_conn_llm() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if not _ensure_workflow_exists(cur, intent):
                    ctx["workflow_error"] = f"未找到可用工作流 intent={intent!r}"
                    return ctx
                steps = _fetch_steps(cur, intent)
    except Exception as e:
        ctx["workflow_error"] = f"读取工作流失败: {e}"
        logger.exception("读取工作流失败 intent=%s", intent)
        return ctx

    if not steps:
        ctx["workflow_error"] = f"工作流 intent={intent!r} 没有定义任何步骤"
        return ctx

    total = len(steps)
    ctx["workflow_total_steps"] = total

    for i, step in enumerate(steps):
        order = int(step.get("step_order") or i + 1)
        sname = (step.get("step_name") or "").strip() or f"step_{order}"
        stype = (step.get("step_type") or "").strip().lower()
        output_key = (step.get("output_key") or "").strip() or None
        on_fail = _normalize_on_fail(step.get("on_fail"))

        prefix = f"[step {i + 1}/{total}] {sname} ({stype})"
        logger.info("%s 开始", prefix)

        try:
            if stype == "query_sql":
                tpl = step.get("sql_template")
                if not isinstance(tpl, str) or not tpl.strip():
                    raise RuntimeError("sql_template 为空")
                _run_query_sql(ctx, tpl, output_key)
            elif stype == "code":
                act = step.get("action")
                if not isinstance(act, str) or not act.strip():
                    raise RuntimeError("action 为空")
                _run_code_step(ctx, act, output_key)
            elif stype == "llm":
                pt = step.get("prompt_template")
                if not isinstance(pt, str):
                    pt = ""
                _run_llm_step(ctx, pt, output_key)
            else:
                raise RuntimeError(f"未知 step_type: {stype!r}")

            ctx.pop("workflow_last_error", None)
            logger.info("%s 完成", prefix)
            if on_step_complete is not None:
                on_step_complete(dict(ctx))
        except Exception as e:
            msg = (
                f"步骤失败 intent={intent!r} step_order={order} "
                f"step_name={sname!r} step_type={stype}: {e}"
            )
            logger.exception("%s 异常: %s", prefix, e)

            if on_fail == "stop":
                raise WorkflowStepError(
                    msg,
                    step_order=order,
                    step_name=sname,
                    step_type=stype,
                ) from e
            if on_fail == "skip":
                ctx["workflow_last_error"] = msg
                logger.warning("%s on_fail=skip，继续下一步", prefix)
                if on_step_complete is not None:
                    on_step_complete(dict(ctx))
                continue
            # ask_user
            ctx["workflow_awaiting_user"] = True
            ctx["workflow_user_message"] = msg
            ctx["workflow_failed_step_order"] = order
            ctx["workflow_failed_step_name"] = sname
            ctx["workflow_failed_step_type"] = stype
            logger.info("%s on_fail=ask_user，返回 context 等待补充", prefix)
            if on_step_complete is not None:
                on_step_complete(dict(ctx))
            return ctx

    return ctx
