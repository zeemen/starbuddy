"""证据补列的推导（specs/evidence-attributes-sprint1/spec.md；迁移 0017）。

平台按请求上下文补齐作答情境、证据等级、输入通道，适配器的证据草稿不带这些字段（插件 API v1.0 svc-#2 / kge-#5）：
- evidence_kind：用过提示（hint_level_used > 0，或适配器证据里有 hint_opened 信号）→ hinted（优先，最保守）；
  没报 hint_level_used → 空（不知道有没有用提示，交给 KGE-04 按保守口径，安全审查必须改 3）；
  否则用途 retest → delayed_retest，transfer_check → transfer，其余 independent；
- evidence_level（LT-04 决定记录（续）第 3 条：由学习服务推导，不接受调用方自报）：键盘作答记 A；申诉一律 C（ADR 15“申诉中”）；
  请求里自报等级（grade 的 request.evidence_level、diagnose 的 ingest.evidence_level）一律 400；
  其他通道（画布、语音、拍照）须有平台侧确认记录才能定级——平台还没有作答证据对象与确认记录（ADR 15 输入通道服务未上线），
  学习服务无从核实，所以一律 400（不再以调用方声明的等级或 answer_evidence_ref 放行，待人确认见 lessons）；
- purpose（同一条）：直接经 HTTP 的写入只能是 practice（缺省），自报其他值 400；复测、迁移检验的用途只由进程内调用方
  按票据写入（trusted_purpose）；
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


def derive(method: str, request: dict, ingest: dict, *, trusted_purpose: bool = False) -> Attributes:
    channel = ingest["channel"]
    purpose = ingest.get("purpose", "practice")
    if purpose != "practice" and not trusted_purpose:
        raise IngestError(400, "INVALID_REQUEST", "ingest.purpose is derived by the learning service; direct writes are practice only")
    if (method == "grade" and request.get("evidence_level") is not None) or ingest.get("evidence_level") is not None:
        raise IngestError(400, "INVALID_REQUEST", "evidence_level is derived by the learning service and cannot be declared")
    if channel != "keypad":
        # 非键盘通道要平台侧确认记录才能定级；平台还没有这类记录（ADR 15 输入通道服务），学习服务无从核实
        raise IngestError(400, "INVALID_REQUEST", "channels other than keypad need a platform confirmation record (not available)")
    if method == "grade" and request.get("appeal") is True:
        # 申诉一律 C（ADR 15“申诉中”；契约审查建议 8）
        return Attributes(channel=channel, evidence_level="C", purpose=purpose)
    return Attributes(channel=channel, evidence_level="A", purpose=purpose)
