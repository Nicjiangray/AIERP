#!/usr/bin/env python3
"""
验证 V14 context_schema 预注入：/ai/chat 固定话术 + 从服务端日志解析 collecting 指标。

需 LLM 可用。默认自动启动 uvicorn（独立端口），将 stdout 写入临时日志文件并解析；
也可设置 E2E_CONTEXT_USE_EXTERNAL_SERVER=1 连接已有 BASE，此时须设置 E2E_LOG_FILE
为服务端日志文件路径（与本次请求时间窗口对齐），否则工具轮次/工具列表为 N/A。

用法:
  cd /Users/nicjiang/aierp && .venv/bin/python scripts/test_context_schema.py

环境变量:
  E2E_CHAT_BASE              默认 http://127.0.0.1:8765（与 E2E_PORT 一致）
  E2E_CHAT_TIMEOUT           单次 HTTP 超时秒数，默认 120
  E2E_PORT                   自动起服务时监听端口，默认 8765
  E2E_CONTEXT_USE_EXTERNAL_SERVER=1  不启动子进程，只连 E2E_CHAT_BASE
  E2E_LOG_FILE               外部模式必填：服务端日志路径（本脚本会读取全文解析）
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, TextIO

import httpx

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.database import db_conn  # noqa: E402

TIMEOUT = float(os.environ.get("E2E_CHAT_TIMEOUT", "120"))
USER_ID = 2
E2E_PORT = int(os.environ.get("E2E_PORT", "8765"))
USE_EXTERNAL = os.environ.get("E2E_CONTEXT_USE_EXTERNAL_SERVER", "").strip() in (
    "1",
    "true",
    "yes",
)
BASE = os.environ.get("E2E_CHAT_BASE", f"http://127.0.0.1:{E2E_PORT}").rstrip("/")
LOG_FILE_ENV = os.environ.get("E2E_LOG_FILE", "").strip()


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


def _chat(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "messages": messages,
        "user_id": USER_ID,
        "stream": False,
        "confirm": False,
    }
    with httpx.Client(timeout=TIMEOUT) as client:
        r = client.post(f"{BASE}/ai/chat", json=body)
        r.raise_for_status()
        return r.json()


def _intent_from_resp(resp: Dict[str, Any]) -> str:
    v = resp.get("intent")
    if v:
        return str(v).strip()
    v = _deep_find_first(resp, "intent")
    return str(v).strip() if v else ""


def _final_status(resp: Dict[str, Any]) -> str:
    if resp.get("confirm_required") is True:
        return "pending_confirm"
    wc = resp.get("workflow_context")
    if isinstance(wc, dict):
        st = str(wc.get("status") or "").strip()
        if st == "pending_confirm":
            return "pending_confirm"
        if st == "error":
            return "error"
    if resp.get("error") or resp.get("error_message"):
        return "error"
    return "reply"


def _split_log_by_collecting_start(log_text: str) -> List[str]:
    """按 [collecting.开始] 分段，每段对应一次 run_collecting 调用。"""
    if not log_text.strip():
        return []
    parts = re.split(r"(?=\[collecting\.开始\])", log_text)
    return [p for p in parts if "[collecting.开始]" in p]


def _parse_collecting_segment(segment: str) -> Tuple[int, List[str], bool]:
    """
    返回: (轮次数, 工具名列表按顺序, 是否出现对 meta_tables/meta_fields 的 query_db)
    """
    round_n = len(re.findall(r"\[collecting\.轮次\]", segment))
    tools = re.findall(r"\[collecting\.tool执行\]\s*name=(\S+)", segment)
    meta_hit = False
    # executor 层 SQL 预览
    for m in re.finditer(r"\[executor\.query_db\].*sql_preview=([^\n]+)", segment):
        prev = m.group(1).lower()
        if "meta_tables" in prev or "meta_fields" in prev:
            meta_hit = True
            break
    # 兜底：query_db 结果块里出现 meta 表名
    if not meta_hit:
        low = segment.lower()
        if "query_db" in low and (
            "meta.meta_tables" in low
            or "meta.meta_fields" in low
            or re.search(r"from\s+meta\.meta_tables", low)
            or re.search(r"from\s+meta\.meta_fields", low)
        ):
            meta_hit = True
    return round_n, tools, meta_hit


def _read_log_path(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


@dataclass
class Scenario:
    label: str
    message: str
    expect_intent: str
    intent: str = ""
    rounds: str = "N/A"
    tools: str = "N/A"
    meta_q: str = "N/A"
    status: str = ""
    note: str = ""


SCENARIOS: List[Scenario] = [
    Scenario(
        label="新建产品（一句话全信息）",
        message="帮我新建一个产品，名称叫测试产品A，品类是电子产品，单位是个，单价100",
        expect_intent="create_simple_entity",
    ),
    Scenario(
        label="创建合同",
        message="帮我建一个合同，客户是华为，内贸，产品是测试产品A，数量10，单价100",
        expect_intent="create_contract",
    ),
    Scenario(
        label="查询客户",
        message="查一下所有客户",
        expect_intent="query_master_data",
    ),
]


def _wait_server_ready(url: str, attempts: int = 60, delay: float = 1.0) -> bool:
    for _ in range(attempts):
        try:
            r = httpx.get(f"{url}/openapi.json", timeout=3)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(delay)
    return False


def _start_uvicorn(log_path: str, port: int) -> Tuple[subprocess.Popen, TextIO]:
    py = os.path.join(_ROOT, ".venv", "bin", "python")
    if not os.path.isfile(py):
        py = sys.executable
    log_f = open(log_path, "w", encoding="utf-8", buffering=1)
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(
        [
            py,
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=_ROOT,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        env=env,
    )
    return proc, log_f


def _print_table(rows: List[Scenario]) -> None:
    headers = ("场景", "意图", "工具轮次", "调用工具列表", "查了meta?", "状态")
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for r in rows:
        tools_short = r.tools if len(r.tools) <= 80 else r.tools[:77] + "..."
        line = (
            f"| {r.label} | {r.intent} | {r.rounds} | {tools_short} | {r.meta_q} | {r.status} |"
        )
        lines.append(line)
    print("\n".join(lines))
    print()


def main() -> int:
    print(
        f"BASE={BASE} TIMEOUT={TIMEOUT}s USE_EXTERNAL={USE_EXTERNAL} LOG_FILE={LOG_FILE_ENV or '(auto tmp)'}\n"
    )

    log_path: Optional[str] = None
    proc: Optional[subprocess.Popen] = None
    log_f = None

    if not USE_EXTERNAL:
        import tempfile

        fd, log_path = tempfile.mkstemp(prefix="aierp_ctx_", suffix=".log")
        os.close(fd)
        proc, log_f = _start_uvicorn(log_path, E2E_PORT)
        if not _wait_server_ready(BASE):
            print("ERROR: 子进程 uvicorn 未在超时内就绪", file=sys.stderr)
            if proc:
                proc.send_signal(signal.SIGTERM)
            return 3
        print(f"已启动 uvicorn pid={proc.pid} 日志={log_path}\n")
    else:
        if not LOG_FILE_ENV:
            print(
                "WARN: 外部模式未设置 E2E_LOG_FILE，工具轮次/工具列表/meta 检测将为 N/A\n",
                file=sys.stderr,
            )
        log_path = LOG_FILE_ENV or None

    try:
        for sc in SCENARIOS:
            _cleanup_collecting_frames()
            if USE_EXTERNAL and log_path:
                _log_before = _read_log_path(log_path)

            try:
                resp = _chat([{"role": "user", "content": sc.message}])
            except Exception as e:
                sc.intent = "HTTP/ERR"
                sc.note = str(e)[:200]
                sc.status = "error"
                continue

            sc.intent = _intent_from_resp(resp)
            sc.status = _final_status(resp)

            if USE_EXTERNAL and log_path:
                time.sleep(0.2)
                _log_after = _read_log_path(log_path)
                chunk = _log_after[len(_log_before) :]
                segs = _split_log_by_collecting_start(chunk)
                seg = segs[0] if segs else chunk
                rn, tools, meta_hit = _parse_collecting_segment(seg)
                sc.rounds = str(rn)
                sc.tools = json.dumps(tools, ensure_ascii=False)
                sc.meta_q = "是" if meta_hit else "否"
            elif not USE_EXTERNAL:
                time.sleep(0.5)

        if not USE_EXTERNAL and log_path and log_f is not None:
            try:
                log_f.flush()
            except Exception:
                pass
            time.sleep(0.8)
            full = _read_log_path(log_path)
            segs = _split_log_by_collecting_start(full)
            for i, sc in enumerate(SCENARIOS):
                if sc.intent == "HTTP/ERR":
                    continue
                if i < len(segs):
                    rn, tools, meta_hit = _parse_collecting_segment(segs[i])
                    sc.rounds = str(rn)
                    sc.tools = json.dumps(tools, ensure_ascii=False)
                    sc.meta_q = "是" if meta_hit else "否"
                else:
                    sc.rounds = "?"
                    sc.tools = "[]"
                    sc.meta_q = "?"

    finally:
        if log_f is not None:
            try:
                log_f.flush()
                log_f.close()
            except Exception:
                pass
        if proc is not None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()

    print("—— V14 context_schema 抽检结果 ——\n")
    _print_table(SCENARIOS)

    # 意图期望提示
    for sc in SCENARIOS:
        if sc.intent and sc.expect_intent and sc.intent != sc.expect_intent:
            print(
                f"NOTE: 场景「{sc.label}」期望意图 {sc.expect_intent}，实际 {sc.intent}",
                file=sys.stderr,
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
