"""判断 → RenderedStatement（契约 insight-service.v0.9 的 RenderedStatement；KGE-04 spec 第 14 节“同一套规则，不另写一套”）。

模板渲染为主，不调模型：结论文字 = 已审核模板的 text_template，只把 {变量} 换成判断的 target_id 与 value 里的编码。
- 变量名只收 [a-z_]，值只收编码（小写编码、节点 ID、有限数字）；缺变量、值不是编码、模板里有其他花括号都不渲染（返回 None）；
- 渲染结果过禁用推断词表检查，命中即不渲染；
- 待验证画虚线（display = dashed），假设与成立画实线；置信度说明按策略分档成编码（few_evidence / moderate / strong）。
模板文案由人写人审后登记（必停项）；测试库里只有合成占位模板。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Mapping

from .shared import Forbidden

VAR = re.compile(r"\{([a-z_]{1,32})\}")
CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,63}$")
MAX_TEXT = 300  # RenderedStatement.text 的 maxLength
VISIBLE = ("hypothesis", "established", "to_verify")


@dataclass(frozen=True)
class ClaimForRender:
    claim_id: str
    template_key: str
    template_version: int
    text_template: str | None
    status: str
    target_id: str
    value: Mapping[str, Any]
    confidence: float


def _code(v: Any) -> str | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return f"{v:.2f}" if math.isfinite(v) else None
    if isinstance(v, str) and CODE.fullmatch(v):
        return v
    return None


def render_text(text_template: str | None, target_id: str, value: Mapping[str, Any], forbidden: Forbidden) -> str | None:
    if not text_template:
        return None
    variables = {"target_id": target_id, "node_id": target_id, **{k: v for k, v in value.items() if isinstance(k, str)}}
    missing = False

    def sub(m: re.Match[str]) -> str:
        nonlocal missing
        code = _code(variables.get(m.group(1)))
        if code is None:
            missing = True
            return ""
        return code

    text = VAR.sub(sub, text_template)
    if missing or "{" in text or "}" in text:
        return None
    text = text.strip()
    if not text or len(text) > MAX_TEXT or forbidden(text):
        return None
    return text


def render_statement(c: ClaimForRender, band: str, forbidden: Forbidden) -> dict[str, Any] | None:
    """返回契约 RenderedStatement；不可渲染（状态不对、没有文本模板、缺变量、命中禁用推断）时返回 None"""
    if c.status not in VISIBLE:
        return None
    text = render_text(c.text_template, c.target_id, c.value, forbidden)
    if text is None:
        return None
    return {"claim_id": c.claim_id, "template_key": c.template_key, "template_version": c.template_version,
            "status": c.status, "display": "dashed" if c.status == "to_verify" else "solid", "text": text,
            "confidence_band": band}
