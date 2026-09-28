"""证据补列的推导（specs/evidence-attributes-sprint1/spec.md；迁移 0017）。

平台按请求上下文补齐作答情境、证据等级、输入通道，适配器的证据草稿不带这些字段（插件 API v1.0 svc-#2 / kge-#5）：
- evidence_kind：用过提示（hint_level_used > 0，或适配器证据里有 hint_opened 信号）→ hinted（优先，最保守）；
  没报 hint_level_used → 空（不知道有没有用提示，交给 KGE-04 按保守口径，安全审查必须改 3）；
  否则用途 retest → delayed_retest，transfer_check → transfer，其余 independent；
- evidence_level：grade 请求的 evidence_level 与 ingest.evidence_level 两处都给时必须一致；都没给时键盘记 A，其余通道必须显式给出；
  拍照最高 B；画布、语音记 A 必须带作答证据引用 answer_evidence_ref（回显、复述确认记录在平台侧，安全审查必须改 1）；
  申诉一律 C（ADR 15“申诉中”）；
- C 级：照常写证据、作答、判分结果（批改要展示），不写能力观察与错因（不进入学习者模型）。
v0 路径（插件契约 0.9.0）没有 ingest：只按提示等级写 evidence_kind，等级与通道为空；申诉同样不写能力观察与错因（安全审查建议 4）。
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
    model_blocked: bool = False  # v0 申诉：没有等级，但同样不进入学习者模型

    def kind_for(self, hint_level_used: int | None, hint_signal: bool = False) -> str | None:
        if hint_signal or (hint_level_used or 0) > 0:
            return "hinted"
        if hint_level_used is None:
            return None
        return PURPOSE_KIND[self.purpose or "practice"]

    @property
    def learner_model_updated(self) -> bool:
        return self.evidence_level != "C" and not self.model_blocked


V0 = Attributes(channel=None, evidence_level=None, purpose=None)


def v0_attributes(method: str, request: dict) -> Attributes:
    return Attributes(None, None, None, model_blocked=method == "grade" and request.get("appeal") is True)


def derive(method: str, request: dict, ingest: dict) -> Attributes:
    channel = ingest["channel"]
    purpose = ingest.get("purpose", "practice")
    given = {v for v in (request.get("evidence_level") if method == "grade" else None, ingest.get("evidence_level")) if v}
    if len(given) > 1:
        raise IngestError(400, "INVALID_REQUEST", "request.evidence_level and ingest.evidence_level disagree")
    level = given.pop() if given else ("A" if channel == "keypad" else None)
    if level is None:
        raise IngestError(400, "INVALID_REQUEST", "evidence_level is required for channels other than keypad")
    if channel == "photo" and level == "A":
        raise IngestError(400, "INVALID_REQUEST", "photo evidence is at most level B")
    if channel in ("canvas", "voice") and level == "A" and not (method == "grade" and request.get("answer_evidence_ref")):
        raise IngestError(400, "INVALID_REQUEST", "level A on canvas or voice needs answer_evidence_ref (confirmation record)")
    if method == "grade" and request.get("appeal") is True:
        level = "C"
    return Attributes(channel=channel, evidence_level=level, purpose=purpose)
