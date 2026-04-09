"""
业务字段值归一化：与数据库 CHECK / 枚举一致，供 tool_executor 与 collecting 共用。
LLM 可能输出中文描述（如「梭织面料」），在 executor 层统一转为库内英文键。
"""

from __future__ import annotations

from typing import Any, Dict


def normalize_products_category(raw: Any) -> str:
    """
    business.products.category：库表约束通常为 knit / woven。
    接受英文枚举或中文「针织/梭织…」描述。
    """
    if raw is None:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    sl = s.lower()
    if sl in ("knit", "woven"):
        return sl
    if "针织" in s:
        return "knit"
    if "梭织" in s:
        return "woven"
    return s


def normalize_entity_fields(table_name: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    """
    按表对 LLM/工具传入的 fields 做就地语义归一化（返回新 dict，不修改入参）。
    新增表/字段时在此扩展。
    """
    tn = (table_name or "").strip()
    out = dict(fields)
    if tn == "products" and "category" in out and out["category"] is not None:
        out["category"] = normalize_products_category(out["category"])
    return out
