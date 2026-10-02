"""判断 → RenderedStatement（契约 insight-service.v0.9 的 RenderedStatement；KGE-04 spec 第 14 节“同一套规则，不另写一套”）。

模板渲染为主，不调模型。文字的渲染规则只有一份：services/learning/learning/mastery/render.py 的 render_text
（学情档案页与这里共用；沿用 shared.py 只读引用学习服务纯函数模块的方式）：
- 结论文字 = 已审核模板的 text_template，只把 {变量} 换成判断的 target_id 与 value 里的编码；
- 变量名只收 [a-z_]，值只收编码（小写编码、节点 ID、有限数字）；缺变量、值不是编码、模板里有其他花括号都不渲染（返回 None）；
- 渲染结果过禁用推断词表检查，命中即不渲染。
这里只负责 RenderedStatement 的外形：待验证画虚线（display = dashed），假设与成立画实线；置信度说明按策略分档成编码
（few_evidence / moderate / strong）。模板文案由人写人审后登记（必停项）；测试库里只有合成占位模板。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .shared import Forbidden  # noqa: F401  先导入 shared：把 services/learning 加进 sys.path
from learning.mastery.render import MAX_TEXT, render_text  # noqa: E402,F401  渲染规则的唯一实现

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
