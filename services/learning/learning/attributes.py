"""证据补列的推导（specs/evidence-attributes-sprint1/spec.md；迁移 0017）。

平台按请求上下文补齐作答情境、证据等级、输入通道，适配器的证据草稿不带这些字段（插件 API v1.0 svc-#2 / kge-#5）：
- evidence_kind：用过提示 → hinted（优先，最保守）；否则用途 retest → delayed_retest，transfer_check → transfer，其余 independent；
- evidence_level：grade 请求的 evidence_level 与 ingest.evidence_level 两处都给时必须一致；都没给时键盘记 A，
  其余通道必须显式给出（画布“回显确认”、语音“复述确认”由前端保证，平台核实不了）；申诉一律 C（ADR 15“申诉中”）；
- C 级：照常写证据、作答、判分结果（批改要展示），不写能力观察与错因（不进入学习者模型）。
v0 路径（插件契约 0.9.0）没有 ingest：只按提示等级写 evidence_kind，等级与通道为空。
"""
from __future__ import annotations

from dataclasses import dataclass

from .errors import IngestError

# 不属于某次作答的载荷类型：不带作答情境（迁移 0017 evidence_event_kind_applies）
NON_ATTEMPT_KINDS = frozenset({"interaction_signal", "speech_features"})
PURPOSE_KIND = {"practice": "independent", "retest": "delayed_retest", "transfer_check": "transfer"}


@dataclass(frozen=True)
class Attributes:
    channel: str | None
    evidence_level: str | None
    purpose: str | None
    map_version_id: str | None = None

    def kind_for(self, hint_level_used: int | None) -> str:
        if (hint_level_used or 0) > 0:
            return "hinted"
        return PURPOSE_KIND[self.purpose or "practice"]

    @property
    def learner_model_updated(self) -> bool:
        return self.evidence_level != "C"


V0 = Attributes(channel=None, evidence_level=None, purpose=None)


def derive(method: str, request: dict, ingest: dict) -> Attributes:
    channel = ingest["channel"]
    purpose = ingest.get("purpose", "practice")
    given = {v for v in (request.get("evidence_level") if method == "grade" else None, ingest.get("evidence_level")) if v}
    if len(given) > 1:
        raise IngestError(400, "INVALID_REQUEST", "request.evidence_level and ingest.evidence_level disagree")
    level = given.pop() if given else ("A" if channel == "keypad" else None)
    if level is None:
        raise IngestError(400, "INVALID_REQUEST", "evidence_level is required for channels other than keypad")
    if method == "grade" and request.get("appeal") is True:
        level = "C"
    return Attributes(channel=channel, evidence_level=level, purpose=purpose)
