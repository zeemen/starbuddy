"""证据分类：一条作答证据对某个单元“计入 / 不计入 + 原因编码”，以及计入时的方向、权重类别与强度（spec 第 3、6 节）。

三个属性并存、互不替代（ADR 15）：evidence_kind 管方向，evidence_level 管能推到哪一层，准入 admission 未准入即按 B 级。
这里只看单条证据；“同族重做只计第一次”“复测轮”“延迟复测间隔”这类跨证据的判定在 gates.replay 里。

采用的待人确认默认：
- #2  提示后做错：不上调，按错因小幅下调，置信度不变；自评“猜的”做对：不计入（guessed）；
- #9  同一题目族只计第一次（gates.replay）；
- #10 没有提示等级：按非低级提示处理（高级提示后做对不计入）；诊断来源没有提示等级按无提示（#23）；
- #11 只因 content_not_admitted 而复核的结果与其他待复核一样不计入；
- #16 只计主节点，次要节点不计入（secondary_node）；
- #23 只计有 grade_result 且有作答引用的证据（no_grade）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .config import MasteryConfig

# 不计入原因（第 13 节；repeat_family、retest_too_early 由 replay 给出）
REPEAT_FAMILY = "repeat_family"
DUPLICATE = "duplicate"
GUESSED = "guessed"
HINT_HEAVY = "hint_heavy"
LOW_CONFIDENCE = "low_confidence"
PENDING_REVIEW = "pending_review"
UNTAGGED = "untagged"
NO_GRADE = "no_grade"                 # 补：没有 grade_result 或没有作答引用（#23）
NOT_SCORED = "not_scored"             # 补：verdict 为 partial / needs_review / not_applicable 等
LEVEL_C = "level_c"                   # 补：C 级不进学习者模型（本不应入库，防御）
SECONDARY_NODE = "secondary_node"     # 补：只作为次要节点出现（#16）
OTHER_UNIT = "other_unit"             # 补：主节点或认知层次不是本单元
# 计入但封顶在“稳定”的原因（迁移及以上只认 A 级、已准入、formal、非旧证据）
NOT_ADMITTED = "not_admitted"
LEVEL_B_CAPPED = "level_b_capped"
NOT_FORMAL = "not_formal"
LEGACY_NO_KIND = "legacy_no_kind"
# replay 的注记
RETEST_TOO_EARLY = "retest_too_early"
DELAYED_TOO_SOON = "delayed_retest_too_soon"

EXCLUSION_CODES = (DUPLICATE, NO_GRADE, UNTAGGED, SECONDARY_NODE, OTHER_UNIT, LEVEL_C, PENDING_REVIEW, NOT_SCORED,
                   LOW_CONFIDENCE, GUESSED, HINT_HEAVY, REPEAT_FAMILY)
CAP_CODES = (NOT_ADMITTED, LEVEL_B_CAPPED, NOT_FORMAL, LEGACY_NO_KIND)
NON_HINTED_KINDS = ("independent", "transfer", "delayed_retest")
KINDS = ("hinted",) + NON_HINTED_KINDS
# 这些原因之前的证据没有有效判分，不占用题目族（待复核结案后仍可按第一次计入）；之后的（猜的、高级提示）已经见过这道族题
_NO_CONSUME = frozenset({DUPLICATE, NO_GRADE, UNTAGGED, SECONDARY_NODE, OTHER_UNIT, LEVEL_C, PENDING_REVIEW, NOT_SCORED,
                         LOW_CONFIDENCE})


@dataclass(frozen=True)
class Unit:
    node_id: str
    cognitive_level: str


@dataclass(frozen=True)
class Evidence:
    """一条作答证据在重算时需要的事实（由第二步从 evidence_event / attempt / grade_result / item_tag / item 读出）。"""
    evidence_id: str
    recorded_at: datetime                  # 服务器时间：一切间隔按它算（#8）
    item_id: str
    source: str = "grade"                  # source_method：grade（日常练习）/ diagnose（平台诊断）
    attempt_id: str | None = None
    family_id: str | None = None           # kge.item_tag.family_id；没有题目族的题按单题自成一族
    item_type: str | None = None           # 题型（来源待人确认 #4）
    tagged: bool = True                    # 有 item_tag 且打标未被驳回
    main_node_id: str | None = None
    secondary_node_ids: tuple[str, ...] = ()
    cognitive_level: str | None = None
    evidence_kind: str | None = None       # 迁移 0017；0017 之前为空
    evidence_level: str | None = None      # A / B / C；0017 之前与 v0 路径为空
    hint_level: int | None = None          # attempt.hint_level_used
    admission: str = "admitted"            # 证据自身与题目当前登记里更严的那个
    has_grade: bool = True                 # 有 grade_result
    has_response_ref: bool = True          # 有作答引用（attempt.response_evidence_id）
    verdict: str | None = "correct"
    grade_confidence: float | None = 1.0
    human_review_required: bool = False
    review_reasons: tuple[str, ...] = ()
    semantics: str | None = "formal"       # grade_result.semantics：formal / practice
    self_report: str | None = None         # attempt.self_report：confident / unsure / guessed（只是作答把握度）
    error_category: str | None = None      # error_attribution 的跨科大类
    occurred_at: datetime | None = None    # 客户端时间：不参与任何计算（#8）

    @property
    def family(self) -> str:
        return self.family_id or f"item:{self.item_id}"


@dataclass(frozen=True)
class Classification:
    counted: bool
    reasons: tuple[str, ...] = ()          # 不计入原因（counted 为假时非空）
    caps: tuple[str, ...] = ()             # 计入但只能推到“稳定”的原因
    kind: str | None = None                # 生效的作答情境
    weight_key: str | None = None          # evidence_weights 的键
    correct: bool = False
    level: str | None = None               # 生效的证据等级（未准入、旧证据按 B）
    consumes_family: bool = False          # 是否占用题目族（之后同族只记录不计入）
    hint_followup: bool = False            # 高级提示后做对：排无提示变式（reason = hint_followup）
    penalty: float = 1.0

    @property
    def strict(self) -> bool:
        """能推动迁移及以上：A 级、已准入、formal、非旧证据。"""
        return self.counted and not self.caps and self.level == "A"

    @property
    def independent_correct(self) -> bool:
        return self.counted and self.correct and self.kind in NON_HINTED_KINDS


def _excluded(*reasons: str, consumes: bool = False, hint_followup: bool = False) -> Classification:
    return Classification(counted=False, reasons=tuple(reasons), consumes_family=consumes, hint_followup=hint_followup)


def classify(ev: Evidence, unit: Unit, cfg: MasteryConfig) -> Classification:
    # 1. 计入口径（#23）：只计有判分、有作答引用的证据
    if not ev.has_grade or not ev.has_response_ref:
        return _excluded(NO_GRADE)
    # 2. 归属（第 1 节）：没有打标或打标被驳回的不计入；只计主节点（#16）
    if not ev.tagged or ev.main_node_id is None or ev.cognitive_level is None:
        return _excluded(UNTAGGED)
    if ev.main_node_id != unit.node_id:
        return _excluded(SECONDARY_NODE if unit.node_id in ev.secondary_node_ids else OTHER_UNIT)
    if ev.cognitive_level != unit.cognitive_level:
        return _excluded(OTHER_UNIT)
    # 3. C 级不进学习者模型（ADR 15）
    if ev.evidence_level == "C":
        return _excluded(LEVEL_C)
    # 4. 待复核一律不推动门槛（ADR 11；#11 默认：只因未准入而复核的也一样）
    if ev.human_review_required:
        return _excluded(PENDING_REVIEW)
    # 5. 判分门槛（规则 3）
    if ev.verdict not in ("correct", "incorrect"):
        return _excluded(NOT_SCORED)
    if ev.grade_confidence is None or ev.grade_confidence < cfg["grade_confidence_min"]:
        return _excluded(LOW_CONFIDENCE)
    correct = ev.verdict == "correct"

    # 6. 作答情境与提示等级
    legacy = ev.evidence_kind is None or ev.evidence_level is None
    hint = ev.hint_level
    if hint is None and ev.source == "diagnose":
        hint = 0  # #23：诊断不提供提示，没有提示等级按无提示
    kind = ev.evidence_kind
    if kind is not None and kind not in KINDS:
        return _excluded(NOT_SCORED)
    if kind is None or kind == "hinted" or (hint or 0) > 0:
        if hint is None or kind == "hinted" or hint > 0:
            kind = "hinted"
        else:
            kind = "independent"  # 旧证据、提示等级为 0：按独立计题目族，权重按更保守的低级提示（第 3 节）
    low_hint = hint is not None and 1 <= hint <= cfg["low_hint_max_level"]

    if correct and ev.self_report == "guessed":
        return _excluded(GUESSED, consumes=True)
    if kind == "hinted" and correct and not low_hint:
        return _excluded(HINT_HEAVY, consumes=True, hint_followup=True)

    # 7. 证据等级与封顶
    caps: list[str] = []
    level = ev.evidence_level or "B"
    if legacy:
        caps.append(LEGACY_NO_KIND)
        level = "B"
    if ev.admission != "admitted":
        caps.append(NOT_ADMITTED)
        level = "B"  # 未准入内容上的证据只能是 B 级（知识服务契约）
    if ev.evidence_level == "B":
        caps.append(LEVEL_B_CAPPED)
    if ev.semantics != "formal":
        caps.append(NOT_FORMAL)

    if correct:
        weight_key = "hinted_low" if kind == "hinted" or legacy else kind
    else:
        weight_key = "hinted_wrong" if kind == "hinted" else "independent_wrong"
    return Classification(counted=True, caps=tuple(caps), kind=kind, weight_key=weight_key, correct=correct, level=level,
                          consumes_family=True, penalty=1.0 if correct else cfg.penalty(ev.error_category))

