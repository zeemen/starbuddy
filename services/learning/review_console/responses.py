"""审核台响应模型（specs/review-console-responses 第二节第 1 条）：每个接口的 2xx 响应都挂一个模型。

- 一律 extra="forbid"：服务层多拼的键在响应校验时报 500（只记接口与多出的键名，不记值），不会悄悄发给前端——返回字段是白名单。
- 路由同时设 response_model_exclude_unset=True：只输出服务层真给了的键，键集合与挂模型之前逐字相同。
  有时不给的键写成 `x: T = None`（可选、给了就不能为空；pydantic 不校验默认值），导出为“非必填、不可空”，对应前端的 `x?: T`。
- 可空的判断：基表列看 NOT NULL；库函数返回的表与 JSON 看代码，拿不准的按可空（运行时宁可前端多处理一个空值，不因空值报 500）。
- 自由结构（差异的旧值 / 新值、变更单签字内容、复核任务的来源快照）写成 Any，不在这里展开。
- 枚举只写库里有约束或代码里写死的取值（Literal）；前端的字面量联合须与之相同（apps/web/tests/review-responses.test.ts）。
导出：scripts/export_review_requests.py --responses → apps/web/src/lib/review-api.responses.json。
"""
from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, model_validator

Level = Literal["low", "medium", "high"]
Decision = Literal["approved", "rejected"]
VersionStatus = Literal["draft", "published", "retired"]
Op = Literal["added", "removed", "changed", "deprecated", "reactivated"]
Severity = Literal["error", "warning"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- 通用小件

class Risk(_M):
    level: Level
    review_level: Level
    reason: str
    structural: bool


class Issue(_M):
    check_name: str
    severity: Severity
    object_id: str
    detail: str


class Check(_M):
    check_name: str
    severity: Severity
    detail: str


class Cursor(_M):
    before: str
    before_id: str


class StatusOut(_M):
    status: str


class ReviewIdOut(_M):
    review_id: str


class PublishedOut(_M):
    published_at: str


# ---------------------------------------------------------------- 登录

class LoginOut(_M):
    token: str


class LogoutOut(_M):
    status: Literal["logged_out"]


class MeOut(_M):
    account_id: str
    login_name: str | None


# ---------------------------------------------------------------- 地图版本

class MapVersion(_M):
    id: str
    subject: str
    version: str
    standard_year: int
    status: VersionStatus
    created_at: str
    published_at: str | None


class MapVersionList(_M):
    items: list[MapVersion]


class NodeRow(_M):
    id: str
    level: str
    parent_id: str | None
    kind: str | None
    name: str
    includes: list[str]
    excludes: list[str]
    examples: list[str]
    scope: str
    cognitive_levels: list[str]
    error_type: str | None
    status: str


class MapChange(_M):
    """地图差异的一条（diff.py）；校验问题挂在 issues（service.attach_issues），采分点另带评分版本"""
    object: str
    key: str
    op: Op
    field: str | None
    old: Any
    new: Any
    risk: Risk
    issues: list[Issue]
    rubric_version: str = None


class DiffSummary(_M):
    level: Level
    review_level: Level
    structural: bool
    required_signers: int
    counts: dict[str, int]
    approvals: int
    latest_decision: Decision | None
    latest_matches: bool


class Impact(_M):
    item_tags: int
    scoring_points: int


class ChangeRequest(_M):
    id: str
    change_type: str
    status: Literal["proposed", "approved", "rejected", "applied"]
    created_at: str
    digest: str
    signed_content: dict[str, Any]
    risk: Risk


class Signoff(_M):
    map_version_id: str
    version: str
    action: Literal["review", "publish", "retire"]
    decision: Decision
    risk_level: Level  # kge.content_review.risk_level NOT NULL
    basis_code: str | None
    digest: str | None
    decided_at: str
    signer: str


class DiffView(_M):
    version: MapVersion
    base_version_id: str | None
    base_version: str | None
    digest: str
    nodes: list[NodeRow]
    summary: DiffSummary
    impact: Impact
    changes: list[MapChange]
    validation: list[Issue]
    unattached_issues: list[Issue]
    change_requests: list[ChangeRequest]
    signoffs: list[Signoff]


class DigestOut(_M):
    digest: str


class PreflightIssue(_M):
    code: str
    detail: str


class StructuralReason(_M):
    source: Literal["diff", "change_request"]
    object: str
    key: str
    op: str
    reason: str


class PreflightSignoff(_M):
    approvals: int
    required: int
    latest_decision: Decision | None
    latest_matches: bool
    enough: bool


class PreflightView(_M):
    version: MapVersion
    base_version_id: str | None
    base_version: str | None
    digest: str
    structural: bool
    required_signers: int
    bump: Literal["patch", "minor", "initial"]
    latest_version: str | None
    suggested_version: str | None
    draft_version: str
    version_ok: bool
    problems: list[PreflightIssue]
    advice: list[PreflightIssue]
    note: str
    structural_reasons: list[StructuralReason]
    counts: dict[str, int]
    risk_level: Level | None
    impact: Impact
    validation_errors: int
    signoff: PreflightSignoff


class RecordRow(_M):
    """版本记录（service.RECORD_COLUMNS）：每行都有全部列，不适用的列为空"""
    line: Literal["map", "page", "policy", "content_version", "admission"]
    map_version_id: str | None
    version: str | None
    action: Literal["review", "publish", "retire", "apply"]
    decision: Decision
    risk_level: Level  # 地图行与页面行取审核记录；考情、内容版本与准入行固定 high
    basis_code: str | None
    digest: str | None
    decided_at: str
    signer: str
    rubric_versions: str
    validation_errors: int | None
    validation_warnings: int | None
    page_id: str | None
    page_commit: str | None
    policy_object: str | None
    policy_object_id: str | None
    city: str | None
    cohort: int | None
    policy_version: str | None
    content_version_id: str | None
    six_segment: str | None
    content_map_version_id: str | None
    item_id: str | None
    from_admission: str | None
    to_admission: str | None
    admission_review_level: str | None


class RecordList(_M):
    items: list[RecordRow]


# ---------------------------------------------------------------- 打标候选

class TagCandidateAlt(_M):
    id: str
    node_id: str
    node_name: str | None
    confidence: str
    layer: str


class TagCandidate(_M):
    id: str
    map_version_id: str
    version: str
    map_status: VersionStatus
    subject: str
    item_id: str
    node_id: str
    node_name: str | None
    node_level: str | None
    role: Literal["main", "secondary"]
    layer: Literal["rule", "vector", "model"]
    confidence: str
    tagger_version: str
    model_version: str | None
    reason_code: str
    grade: Literal["A", "B"]
    item_source_kind: str
    status: str
    created_at: str
    alternatives: list[TagCandidateAlt]


class TagCursor(_M):
    after_confidence: str
    after_id: str


class TagWorklist(_M):
    items: list[TagCandidate]
    next: TagCursor | None
    open_total: int


class TagDecideOut(_M):
    status: Literal["written", "recorded"]
    decision: Literal["accept", "reject", "relabel"]
    decided_by: str


# ---------------------------------------------------------------- 内容版本与准入

class ContentVersionRow(_M):
    id: str
    region: str
    exam_year: int
    grade: int
    textbook_version: str
    subject: str
    content_version: str
    status: VersionStatus
    map_version_id: str | None
    published_at: str | None
    retired_at: str | None
    skill_nodes: int
    items: int
    six_segment: str


class ContentVersionList(_M):
    items: list[ContentVersionRow]


class ContentVersionHead(_M):
    """内容版本快照里的版本行（platform.content_version_snapshot）：没有登记计数"""
    id: str
    region: str
    exam_year: int
    grade: int
    textbook_version: str
    subject: str
    content_version: str
    status: VersionStatus
    map_version_id: str | None
    published_at: str | None
    retired_at: str | None
    six_segment: str


class ContentBase(_M):
    id: str
    content_version: str
    status: VersionStatus


class MapBrief(_M):
    id: str
    subject: str
    version: str
    status: VersionStatus


class ContentChange(_M):
    """内容版本与考情方案的差异（content.py）：不挂校验问题"""
    object: str
    key: str
    op: Op
    field: str | None
    old: Any
    new: Any
    risk: Risk


class ContentSummary(_M):
    level: Level
    review_level: Level
    required_signers: int
    counts: dict[str, int]
    admission_changes: int
    latest_decision: Decision | None
    latest_matches: bool
    ready: bool


class ContentItem(_M):
    item_id: str
    admission: str
    source_ref: str | None
    source_type: str
    license_status: str
    skill_local_id: str | None
    admission_digest: str


class ReleaseRecord(_M):
    """platform.release_record_export（迁移 0042）的一行"""
    id: str
    line: Literal["content_version", "admission"]
    action: Literal["review", "publish", "retire", "apply"]
    curriculum_version_id: str
    region: str
    exam_year: int
    grade: int
    textbook_version: str
    content_version: str
    map_version_id: str | None
    item_id: str | None
    decision: Decision
    digest: str | None
    basis_code: str | None
    from_admission: str | None
    to_admission: str | None
    review_level: str | None
    applies_review_id: str | None
    affected_rubric_versions: list[str] | None
    decided_at: str
    signer: str


class ContentView(_M):
    version: ContentVersionHead
    base_version: ContentBase | None
    digest: str
    map_version: MapBrief | None
    summary: ContentSummary
    changes: list[ContentChange]
    checks: list[Check]
    items: list[ContentItem]
    records: list[ReleaseRecord]
    pending_admissions: list[ReleaseRecord]


class AdmissionApplyOut(_M):
    applied: int
    recompute_since: str
    recompute: str


# ---------------------------------------------------------------- 考情政策

class SchemeListRow(_M):
    id: str
    city: str
    cohort: int
    version: str
    status: VersionStatus
    source_document_id: str
    published_at: str | None
    created_at: str


class SchemeRow(_M):
    id: str
    city: str
    cohort: int
    version: str
    status: VersionStatus
    source_document_id: str
    published_at: str | None


class PolicyDraft(_M):
    object_type: Literal["source_document", "admission_line", "school", "control_line"]
    id: str
    city: str
    cohort: int | None
    label: str
    digest: str
    approvals: int


class PolicyList(_M):
    schemes: list[SchemeListRow]
    drafts: list[PolicyDraft]


class SchemeSubject(_M):
    subject: str
    scored: bool
    full_score: str | None
    proposition: str
    exam_form: str
    duration_minutes: int | None
    combined_group: str | None
    taken_in_grade: int
    exam_month: int | None
    eligibility_effect: str


class SchemeBatch(_M):
    batch_code: str
    batch_order: int
    track: str
    control_line_basis: str


class SchemeTransition(_M):
    change_kind: str
    replaces_scheme_id: str | None


class PolicySummary(_M):
    level: Level
    review_level: Level
    required_signers: int
    counts: dict[str, int]
    approvals: int


class PolicyRecord(_M):
    """platform.policy_signoff_record() 的一行（方案的签字与发布）"""
    object_type: str
    object_id: str
    city: str | None
    version: str | None
    cohort: int | None
    action: str
    decision: Decision
    digest: str | None
    signed_at: str
    signer: str


class PolicyView(_M):
    scheme: SchemeRow
    digest: str
    total: str | None
    subjects: list[SchemeSubject]
    batches: list[SchemeBatch]
    transitions: list[SchemeTransition]
    previous_version: SchemeRow | None
    previous_cohort: SchemeRow | None
    diff_previous_version: list[ContentChange]
    diff_previous_cohort: list[ContentChange]
    validation: list[Issue]
    summary: PolicySummary
    records: list[PolicyRecord]


class ApprovalsOut(_M):
    approvals: int


class SourceDomain(_M):
    host: str
    issuer: str
    status: Literal["active", "retired"]
    added_at: str


class SourceDomainList(_M):
    items: list[SourceDomain]


class SourceChangeOut(_M):
    host: str
    status: Literal["active", "retired"]


# ---------------------------------------------------------------- 编译页面与抽检

class PageEntry(_M):
    page_id: str
    mr_id: str
    head_commit: str
    merge_commit: str | None
    risk: Level
    required: int
    approvals_platform: int
    approvals_review: int
    url: str
    state: Literal["merged", "open"]
    status: Literal["ok", "failed"] = None
    problems: list[str] = None


class PageSuperseded(_M):
    page_id: str
    mr_id: str
    head_commit: str
    merge_commit: str | None


class PageOrphan(_M):
    page_id: str
    page_commit: str
    records: int
    problems: list[str]
    blocking: bool


class PagesView(_M):
    """没配置时只有 configured = false；配置了时是对账报告（pages.reconcile）"""
    configured: bool
    format: str = None
    repository: str = None
    branch: str = None
    exported_at: str = None
    ok: bool = None
    merged: list[PageEntry] = None
    superseded: list[PageSuperseded] = None
    pending: list[PageEntry] = None
    orphans: list[PageOrphan] = None
    held_pages: list[str] = None


class PageSignOut(_M):
    review_id: str
    risk_level: Level


class SpotCheckRecord(_M):
    decision: Decision
    signer: str
    decided_at: str


class SpotCheckSample(_M):
    page_id: str
    mr_id: str
    head_commit: str
    url: str
    checks: list[SpotCheckRecord]


class SpotCheckView(_M):
    configured: bool
    ratio: float
    window: str
    day: str
    basis_code: str
    warnings: list[str]
    candidates: int = None
    sampled: list[SpotCheckSample] = None


# ---------------------------------------------------------------- 复核队列

QueueStatus = Literal["open", "in_progress", "resolved"]
# 与 platform.review_task 的 source_kind CHECK 一致（0036 加了周报抽检；tests/test_review_responses_enums.py 连库核对）
SourceKind = Literal["grade_result", "skill_observation", "error_attribution", "claim", "diagnosis_step", "weekly_report"]


class QueueItem(_M):
    id: str
    subject: str
    student_ref: str
    source_kind: SourceKind
    source_id: str
    reasons: list[str]
    status: QueueStatus
    created_at: str
    assigned: bool
    assigned_to_me: bool
    assigned_at: str | None
    resolution_code: str | None
    resolved_at: str | None


class QueueList(_M):
    items: list[QueueItem]
    next: Cursor | None


# 复核任务的来源快照（platform.review_task_detail，0036 的最新定义；specs/review-task-source-models）：按 source_kind 各一种结构，
# 能力观察与错因归因没有分支（source 为空）。可空按基表列的 NOT NULL 与库函数里的 LEFT JOIN。
class GradeDimensionSource(_M):
    dimension_id: str
    score: float | None
    max_score: float
    judged_by: Literal["verifier", "rubric_rule", "rubric_model"]
    subjective: bool
    rule_ref: str | None
    confidence: float
    evidence_ids: list[str]


class GradeSource(_M):
    verdict: Literal["correct", "incorrect", "partial", "needs_review", "not_applicable"]
    score_total: float | None
    max_total: float
    confidence: float
    calibrated: bool
    rubric_version: str
    source_version: str
    model_version: str
    review_reasons: list[str]
    evidence_ids: list[str]
    superseded: bool
    created_at: str
    dimensions: list[GradeDimensionSource]


class DiagnosisStepSource(_M):
    """诊断步骤（0027）：结论与证据来自 LEFT JOIN 的判分，没有判分时为空；不给过程原文（审核台按证据 ID 另取，授权待人确认）"""
    kind: Literal["adaptive", "process", "explain"]
    node_id: str
    item_id: str | None
    grade_result_id: str | None
    work_evidence_id: str | None
    evidence_ids: list[str] | None
    verdict: Literal["correct", "incorrect", "partial", "needs_review", "not_applicable"] | None
    answered_at: str | None


class ClaimSource(_M):
    template_key: str
    template_version: int
    target_type: Literal["node", "skill", "metric", "exam", "preference"]
    status: Literal["hypothesis", "established", "to_verify", "refuted", "withdrawn"]
    confidence: float
    source_type: Literal["inferred", "self", "guardian", "objective"]
    model_version: str | None
    evidence_ids: list[str]
    created_at: str


class WeeklySentence(_M):
    """周报的一句（insight.valid_weekly_sentences 的键白名单）：claim_revision 由写入守卫盖上；label 只给假设；置信度分档只在家长版"""
    claim_id: str
    template_key: str
    template_version: int
    section: Literal["lit", "working_on", "next", "progress", "stuck", "suggestion", "prediction"]
    text: str
    claim_revision: str
    label: Literal["preliminary"] = None
    confidence_band: Literal["few_evidence", "moderate", "strong"] = None


class WeeklyReportSource(_M):
    audience: Literal["student", "guardian"]
    week_start: str
    week_end: str
    status: Literal["generated", "withheld"]
    sentences: list[WeeklySentence]
    algorithm_version: str
    policy_version: str
    generated_at: str


# 来源种类 → 来源模型（None：库函数不给来源）。导出与前端核对按这张表展开成分支结构
SOURCE_BY_KIND: dict[str, type[_M] | None] = {
    "grade_result": GradeSource, "diagnosis_step": DiagnosisStepSource, "claim": ClaimSource, "weekly_report": WeeklyReportSource,
    "skill_observation": None, "error_attribution": None,
}


class QueueTask(QueueItem):
    """platform.review_task_detail 的 JSON：列表行再加是否本人结案与来源快照；来源按 source_kind 校验（SOURCE_BY_KIND）"""
    resolved_by_me: bool
    source: dict[str, Any] | None

    # 导出脚本据此把 source 展开成按 source_kind 分支的结构
    TAGGED: ClassVar[dict[str, tuple[str, dict[str, type[_M] | None]]]] = {"source": ("source_kind", SOURCE_BY_KIND)}

    @model_validator(mode="after")
    def _source_matches_kind(self) -> QueueTask:
        model = SOURCE_BY_KIND[self.source_kind]
        if model is None:
            if self.source is not None:
                raise ValueError(f"来源种类 {self.source_kind} 不该有来源快照")
        elif self.source is None:
            raise ValueError(f"来源种类 {self.source_kind} 缺来源快照")
        else:
            model.model_validate(self.source)
        return self


class ResolutionCode(_M):
    code: str
    label: str
    applies_to: list[str]


class ResolutionCodeList(_M):
    items: list[ResolutionCode]


class TaskStatusOut(_M):
    status: QueueStatus


# ---------------------------------------------------------------- 内部成员测试运营（LT-06）

FeedbackStatus = Literal["new", "triaged", "closed"]


class FeedbackRow(_M):
    id: str
    source: str
    category: str
    body: str
    route_template: str | None
    status: FeedbackStatus
    created_at: str
    status_changed_at: str | None
    student_ref: str | None


class FeedbackList(_M):
    items: list[FeedbackRow]
    next: Cursor | None


class FeedbackStatusOut(_M):
    status: FeedbackStatus


class InternalTestExitRow(_M):
    student_ref: str
    exited_at: str
    consents_revoked: list[str]
    consent_active: bool
    sessions_revoked: int
    erasure_status: str | None
    erased: bool


class InternalTestExitList(_M):
    items: list[InternalTestExitRow]


# ---------------------------------------------------------------- 路由 → 响应模型（app.py 按这张表挂；导出与覆盖核对也用它）

ROUTES: dict[str, type[_M]] = {
    "POST /review/v0/auth/login": LoginOut,
    "POST /review/v0/auth/logout": LogoutOut,
    "GET /review/v0/auth/me": MeOut,
    "GET /review/v0/map-versions": MapVersionList,
    "GET /review/v0/map-versions/{version_id}/diff": DiffView,
    "PATCH /review/v0/map-versions/{version_id}/nodes/{node_id}": DigestOut,
    "POST /review/v0/map-versions/{version_id}/sign": ReviewIdOut,
    "POST /review/v0/map-changes/{change_id}/sign": StatusOut,
    "POST /review/v0/map-versions/{version_id}/publish": PublishedOut,
    "POST /review/v0/map-versions/{version_id}/retire": StatusOut,
    "GET /review/v0/queue": QueueList,
    "GET /review/v0/queue/resolution-codes": ResolutionCodeList,
    "GET /review/v0/queue/{task_id}": QueueTask,
    "POST /review/v0/queue/{task_id}/take": TaskStatusOut,
    "POST /review/v0/queue/{task_id}/release": TaskStatusOut,
    "POST /review/v0/queue/{task_id}/resolve": TaskStatusOut,
    "GET /review/v0/pages": PagesView,
    "POST /review/v0/pages/sign": PageSignOut,
    "GET /review/v0/spot-checks": SpotCheckView,
    "POST /review/v0/spot-checks/sign": ReviewIdOut,
    "GET /review/v0/content-versions": ContentVersionList,
    "GET /review/v0/content-versions/{cv}/diff": ContentView,
    "POST /review/v0/content-versions/{cv}/sign": ReviewIdOut,
    "POST /review/v0/content-versions/{cv}/publish": PublishedOut,
    "POST /review/v0/content-versions/{cv}/retire": StatusOut,
    "POST /review/v0/admission/sign": ReviewIdOut,
    "POST /review/v0/admission/apply": AdmissionApplyOut,
    "GET /review/v0/policy": PolicyList,
    "GET /review/v0/policy/schemes/{scheme}": PolicyView,
    "POST /review/v0/policy/schemes/{scheme}/sign": ApprovalsOut,
    "POST /review/v0/policy/schemes/{scheme}/publish": PublishedOut,
    "POST /review/v0/policy/records/{object_id}/sign": StatusOut,
    "GET /review/v0/policy/sources": SourceDomainList,
    "POST /review/v0/policy/sources": SourceChangeOut,
    "POST /review/v0/policy/sources/retire": SourceChangeOut,
    "GET /review/v0/records": RecordList,
    "GET /review/v0/map-versions/{version_id}/preflight": PreflightView,
    "GET /review/v0/tag-candidates": TagWorklist,
    "POST /review/v0/tag-candidates/{candidate_id}/decide": TagDecideOut,
    "GET /review/v0/feedback": FeedbackList,
    "POST /review/v0/feedback/{feedback_id}/status": FeedbackStatusOut,
    "GET /review/v0/internal-test/exits": InternalTestExitList,
}
