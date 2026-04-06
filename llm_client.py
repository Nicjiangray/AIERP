# DEPRECATED: 已迁移至 llm/client.py，请勿引用
# 已迁移至 llm/client.py，待确认后删除
from __future__ import annotations

import base64
import os
from io import BytesIO
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import openai
from openai import OpenAI, AsyncOpenAI

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[misc, assignment]


def _load_dotenv_if_available() -> None:
    if not load_dotenv:
        return
    env_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(env_path)


_load_dotenv_if_available()

_DEFAULT_MODEL = "qwen3.5:35b"
_DEFAULT_BASE = "http://localhost:11434/v1"
_DEFAULT_KEY = "ollama"


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    if raw is None:
        return default
    s = raw.strip()
    return s if s else default


def _env_llm_api_key() -> str:
    """未设置环境变量时沿用 Ollama 占位 key；若显式设置为空字符串则保持为空（便于发现未填 Key）。"""
    if "LLM_API_KEY" not in os.environ:
        return _DEFAULT_KEY
    return (os.environ.get("LLM_API_KEY") or "").strip()


LLM_MODEL_NAME = _env_str("LLM_MODEL_NAME", _DEFAULT_MODEL)
LLM_BASE_URL = _env_str("LLM_BASE_URL", _DEFAULT_BASE)
LLM_API_KEY = _env_llm_api_key()

# 连接与请求超时（秒）
LLM_TIMEOUT_SECONDS = 120

# Ollama OpenAI 兼容层：对 Qwen 等「thinking」模型，省略时会默认开启推理，极慢。
# 见 Ollama 文档/issue：`reasoning_effort` 为 "none" 时关闭 thinking。
LLM_REASONING_EFFORT = "none"

_client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)
_async_client = AsyncOpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)

# DeepSeek（或其它 OpenAI 兼容云）fallback；FALLBACK_API_KEY 默认空，未配置则无法在本地连续失败后切换
_FALLBACK_MODEL = _env_str("FALLBACK_MODEL_NAME", "deepseek-chat")
_FALLBACK_BASE = _env_str("FALLBACK_BASE_URL", "https://api.deepseek.com/v1")
_FALLBACK_KEY = (os.environ.get("FALLBACK_API_KEY") or "").strip()

_fb_key_for_client = _FALLBACK_KEY if _FALLBACK_KEY else "missing-fallback-key"
_fallback_client = OpenAI(base_url=_FALLBACK_BASE, api_key=_fb_key_for_client)
_async_fallback_client = AsyncOpenAI(base_url=_FALLBACK_BASE, api_key=_fb_key_for_client)

_LOCAL_RETRYABLE = (
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.RateLimitError,
    openai.APIError,
)


def llm_openai_extra_body() -> Optional[Dict[str, Any]]:
    """仅对本地 Ollama（及显式开启时）附加 reasoning_effort；DeepSeek 等云端 API 不传 extra_body。"""
    if os.environ.get("LLM_FORCE_REASONING_EFFORT", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return {"reasoning_effort": LLM_REASONING_EFFORT}
    u = (LLM_BASE_URL or "").lower()
    if "11434" in u:
        return {"reasoning_effort": LLM_REASONING_EFFORT}
    return None


def _connection_hint() -> str:
    return (
        f"LLM 连接失败：无法访问 {LLM_BASE_URL}。"
        "请检查网络、LLM_BASE_URL、API Key 及服务是否可用（本机 Ollama 需已启动）。"
    )


def chat(messages: List[Dict[str, Any]], images: Optional[List[str]] = None) -> str:
    """
    messages: OpenAI ChatCompletions 格式的消息列表，例如：
      [{"role": "system"|"user"|"assistant", "content": "..."}, ...]
    images: 预留参数（base64 列表）。当前版本先不实现图片逻辑。

    return: AI 回复的纯文字字符串
    """
    # 预留接口：暂不实现图片逻辑，但保留签名以便后续扩展
    if images:
        # 为避免未来调用误用，现在明确提示而不是默默忽略
        raise RuntimeError("当前版本仅支持文字对话，图片尚未实现。")

    try:
        kwargs: Dict[str, Any] = {
            "model": LLM_MODEL_NAME,
            "messages": messages,
            "timeout": LLM_TIMEOUT_SECONDS,
        }
        eb = llm_openai_extra_body()
        if eb is not None:
            kwargs["extra_body"] = eb
        resp = _client.chat.completions.create(**kwargs)
        content = (resp.choices[0].message.content or "").strip()
        return content
    except openai.APITimeoutError as e:
        raise RuntimeError(f"LLM 请求超时：{e}") from e
    except openai.APIConnectionError as e:
        raise RuntimeError(_connection_hint()) from e
    except openai.RateLimitError as e:
        raise RuntimeError(f"LLM 被限流，请稍后重试：{e}") from e
    except openai.APIError as e:
        raise RuntimeError(f"LLM 请求失败：{e}") from e


async def chat_stream(messages: List[Dict[str, Any]], images: Optional[List[str]] = None):
    """
    与 chat() 相同参数，异步流式返回正文增量（用于 SSE）。
    同样附带 reasoning_effort=none，避免 Ollama 上 Qwen 等默认开 thinking。
    """
    if images:
        raise RuntimeError("当前版本仅支持文字对话，图片尚未实现。")
    try:
        kwargs: Dict[str, Any] = {
            "model": LLM_MODEL_NAME,
            "messages": messages,
            "timeout": LLM_TIMEOUT_SECONDS,
            "stream": True,
        }
        eb = llm_openai_extra_body()
        if eb is not None:
            kwargs["extra_body"] = eb
        stream = await _async_client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content
    except openai.APITimeoutError as e:
        raise RuntimeError(f"LLM 请求超时：{e}") from e
    except openai.APIConnectionError as e:
        raise RuntimeError(_connection_hint()) from e
    except openai.RateLimitError as e:
        raise RuntimeError(f"LLM 被限流，请稍后重试：{e}") from e
    except openai.APIError as e:
        raise RuntimeError(f"LLM 请求失败：{e}") from e


def chat_with_fallback(
    messages: List[Dict[str, Any]], max_retries: int = 3
) -> str:
    """
    先对本地 LLM 最多尝试 max_retries 次（含 extra_body 规则与 chat() 一致）；
    均失败后使用 fallback 云端（不传 extra_body）。未配置 FALLBACK_API_KEY 时抛错。
    """
    last_err: Optional[BaseException] = None
    for _ in range(max_retries):
        try:
            kwargs: Dict[str, Any] = {
                "model": LLM_MODEL_NAME,
                "messages": messages,
                "timeout": LLM_TIMEOUT_SECONDS,
            }
            eb = llm_openai_extra_body()
            if eb is not None:
                kwargs["extra_body"] = eb
            resp = _client.chat.completions.create(**kwargs)
            return (resp.choices[0].message.content or "").strip()
        except _LOCAL_RETRYABLE as e:
            last_err = e
            continue
    if not _FALLBACK_KEY:
        raise RuntimeError(
            f"本地 LLM 已连续失败 {max_retries} 次，且未配置 FALLBACK_API_KEY，无法使用云端 fallback。"
        ) from last_err
    try:
        resp = _fallback_client.chat.completions.create(
            model=_FALLBACK_MODEL,
            messages=messages,
            timeout=LLM_TIMEOUT_SECONDS,
        )
        return (resp.choices[0].message.content or "").strip()
    except openai.APITimeoutError as e:
        raise RuntimeError(f"LLM fallback 请求超时：{e}") from e
    except openai.APIConnectionError as e:
        raise RuntimeError(
            f"LLM fallback 连接失败：无法访问 {_FALLBACK_BASE}。请检查网络与 FALLBACK_API_KEY。"
        ) from e
    except openai.RateLimitError as e:
        raise RuntimeError(f"LLM fallback 被限流：{e}") from e
    except openai.APIError as e:
        raise RuntimeError(f"LLM fallback 请求失败：{e}") from e


async def chat_stream_with_fallback(
    messages: List[Dict[str, Any]], max_retries: int = 3
) -> AsyncIterator[str]:
    """
    流式版 chat_with_fallback：本地最多 max_retries 次，失败后走 fallback（无 extra_body）。
    """
    last_err: Optional[BaseException] = None
    for _ in range(max_retries):
        try:
            kwargs: Dict[str, Any] = {
                "model": LLM_MODEL_NAME,
                "messages": messages,
                "timeout": LLM_TIMEOUT_SECONDS,
                "stream": True,
            }
            eb = llm_openai_extra_body()
            if eb is not None:
                kwargs["extra_body"] = eb
            stream = await _async_client.chat.completions.create(**kwargs)
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    yield delta.content
            return
        except _LOCAL_RETRYABLE as e:
            last_err = e
            continue
    if not _FALLBACK_KEY:
        raise RuntimeError(
            f"本地 LLM 已连续失败 {max_retries} 次，且未配置 FALLBACK_API_KEY，无法使用云端 fallback。"
        ) from last_err
    try:
        stream = await _async_fallback_client.chat.completions.create(
            model=_FALLBACK_MODEL,
            messages=messages,
            timeout=LLM_TIMEOUT_SECONDS,
            stream=True,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content
    except openai.APITimeoutError as e:
        raise RuntimeError(f"LLM fallback 请求超时：{e}") from e
    except openai.APIConnectionError as e:
        raise RuntimeError(
            f"LLM fallback 连接失败：无法访问 {_FALLBACK_BASE}。请检查网络与 FALLBACK_API_KEY。"
        ) from e
    except openai.RateLimitError as e:
        raise RuntimeError(f"LLM fallback 被限流：{e}") from e
    except openai.APIError as e:
        raise RuntimeError(f"LLM fallback 请求失败：{e}") from e


def parse_file(file_path: str) -> Dict[str, Any]:
    """
    解析用户上传文件（全内存完成，不生成临时文件）。

    约定：前端把文件编码成字符串传入本函数：
      file_path: "<filename>|<base64>"
    其中 <base64> 为文件原始二进制的 base64（不带 data: 前缀）。

    返回：
      图片(jpg/png/webp)：{"type": "images", "content": [base64字符串], "summary": "..."}
      Excel(xlsx/xls)：{"type": "text", "content": "Markdown表格字符串", "summary": "..."}
      PDF：优先文字；文字长度 > 50 字符则返回文字；否则返回图片 base64 列表
    """
    if not file_path or not str(file_path).strip():
        raise RuntimeError("文件参数为空，无法解析。")

    if "|" not in file_path:
        raise RuntimeError("文件参数格式非法，期望：<filename>|<base64>。")

    filename, b64 = file_path.split("|", 1)
    filename = (filename or "").strip()
    b64 = (b64 or "").strip()
    if not filename:
        filename = "upload"
    if not b64:
        raise RuntimeError(f"文件内容为空：{filename}")

    try:
        raw = base64.b64decode(b64, validate=False)
    except Exception as e:
        raise RuntimeError(f"文件 base64 解码失败：{filename}，原因：{e}") from e

    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""

    # --- 图片：转成 base64（保持原样即可，但这里做一次标准化编码）---
    if ext in {"jpg", "jpeg", "png", "webp"}:
        b64_norm = base64.b64encode(raw).decode()
        summary = f"已读取 图片：{filename}"
        return {"type": "images", "content": [b64_norm], "summary": summary}

    # --- Excel：所有 sheet 读入，转 Markdown 表格（>100行截断）---
    if ext in {"xlsx", "xls"}:
        import pandas as pd

        bio = BytesIO(raw)
        try:
            dfs = pd.read_excel(bio, sheet_name=None, dtype=str)
        except Exception as e:
            raise RuntimeError(f"Excel 解析失败：{filename}，原因：{e}") from e

        sheet_cnt = len(dfs)
        total_rows = 0
        md_parts: List[str] = []
        truncated = False

        for sheet_name, df in dfs.items():
            if df is None:
                continue

            df = df.fillna("")
            n = len(df.index)
            total_rows += n

            note = ""
            if n > 100:
                truncated = True
                df = df.head(100)
                note = f"（仅展示前 100 行，实际 {n} 行）"

            # to_markdown 需要列名；dtype=str 已尽量避免 dtype 混乱
            md_table = df.to_markdown(index=False)
            md_parts.append(f"### Sheet: {sheet_name}\n{note}\n\n{md_table}".strip())

        if not md_parts:
            md_str = "(Excel 无可解析数据)"
            total_rows = 0
        else:
            md_str = "\n\n".join(md_parts)

        summary = f"已读取 Excel，共 {sheet_cnt} 个 sheet，{total_rows} 行数据"
        return {"type": "text", "content": md_str, "summary": summary}

    # --- PDF：先抽文字；若文字长度 <= 50 则转图片 base64 ---
    if ext == "pdf":
        import pdfplumber
        from pdf2image import convert_from_bytes

        bio = BytesIO(raw)
        try:
            with pdfplumber.open(bio) as pdf:
                pages_text: List[str] = []
                for p in pdf.pages:
                    t = p.extract_text() or ""
                    pages_text.append(t)
        except Exception as e:
            raise RuntimeError(f"PDF 文本提取失败：{filename}，原因：{e}") from e

        full_text = "\n".join(pages_text).strip()
        if len(full_text) > 50:
            summary = f"已读取 PDF，文字长度 {len(full_text)} 字"
            return {"type": "text", "content": full_text, "summary": summary}

        # 文字较少：按图片处理
        try:
            pil_images = convert_from_bytes(raw, dpi=200)
        except Exception as e:
            raise RuntimeError(
                f"PDF 转图片失败：{filename}，原因：{e}。"
                "通常需要安装 poppler（例如：brew install poppler）。"
            ) from e

        b64_pages: List[str] = []
        for img in pil_images:
            out = BytesIO()
            img.save(out, format="PNG")
            b64_pages.append(base64.b64encode(out.getvalue()).decode())

        summary = f"已读取 PDF，按图片解析，共 {len(b64_pages)} 页"
        return {"type": "images", "content": b64_pages, "summary": summary}

    raise RuntimeError(f"不支持该文件类型：{filename}（扩展名 .{ext}）")
