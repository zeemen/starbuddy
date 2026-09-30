"""判断生成与生命周期（spec 设计要点 2；SIE V1.1 图 2；ADR 13）：纯函数，不连库。

输入：KGE 掌握估计（每“节点 × 认知层次”一行，形状按 KGE-04 spec 第 13 节：门槛、p、置信度、证据 ID、下次复测时间），
现有判断，策略配置，当前时间。输出：按顺序执行的写入操作（新建或修改；每次修改都带原因，由 store 在同一事务里执行）。
KGE-04 第二步（节点键写库）落地前由合成掌握行驱动；这里不猜数据库的新列，只认 MasteryRow。

规则（每条都对应 0010 insight.change_allowed 的一行，见 ALLOWED；有测试逐行核对迁移文件）：
- 每个“节点 × 层次”至多一条活着的“知识掌握”判断（门槛在策略 mastery_gates 里时生成），门槛到“迁移”及以上时再有一条
  “知识迁移”判断；basis_kind = mastery_estimate，basis_id = 当前掌握行；value 只放渲染变量 {level, gate}。
- 新建：策略已签字且（门槛 ≥ establish.min_gate、置信度 ≥ confidence_min、证据 ≥ evidence_min）→ 成立；否则假设。
  未签字的策略下永远只到“假设”（to_verify → established 也不走）。
- 到期（expires_at ≤ now）：假设 / 成立 → 待验证（expired）。到期时间 = now + 有效期，若下次复测时间更早则取复测时间：
  复测到了判断就到期，复测结果就是验证（验证题由 KGE 复测产生）。
- 待验证 + 到期之后出现的新证据（掌握行更新且带新证据 ID）：仍支持原判断 → 成立（verification_supported，需签字与阈值）；
  不支持（门槛低于判断所述）→ 推翻（verification_refuted）。
- 依据被更正 → 撤回（fact_corrected）：该单元没有当前掌握行了（节点撤下、依据行不再是当前行），或新掌握行丢掉了判断引用过的证据。
  偏差（lessons 待人确认）：门槛在判断到期前下降（KGE 设计上不应出现，只在复测后退级）时也按撤回处理，借用 fact_corrected。
- 其余变化（新依据行、门槛上升、置信度、证据增加）→ revised，不延长到期时间（不能靠续期绕开待验证）；只有验证支持时续期。
- 可见范围：只有策略 visible_subjects（首发只数学）里的学科对 student / guardian 可见，其余只给 agent；不超过模板上限；
  可见范围变化单独一次修改（visibility_changed）。
同样的输入永远得到同样的操作序列（排序确定，不读系统时钟）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

from .policy import NODE_CATEGORIES, Policy
from .shared import COGNITIVE_LEVELS, GATE_RANK, GATES, STAR_GATES

ALGORITHM = "sie-claims"
ALGORITHM_VERSION = "0.1.0"
LIVE = ("hypothesis", "established", "to_verify")
TERMINAL = ("refuted", "withdrawn")
MAX_EVIDENCE = 200  # ClaimSummary.evidence_ids 的 maxItems
NODE_ID = re.compile(r"^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$")  # knowledge-service NodeId
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
VERSIONED_NAME = re.compile(r"^[a-z0-9][a-z0-9\-]{0,47}@[A-Za-z0-9][A-Za-z0-9._\-]{0,31}$")  # platform.versioned_name
AUDIENCE_ORDER = ("agent", "student", "guardian")

# 0010 insight.change_allowed 的白名单（原状态, 新状态, 原因）；None = 新建
ALLOWED: frozenset[tuple[str | None, str, str]] = frozenset({
    (None, "hypothesis", "created"), (None, "established", "created"),
    ("hypothesis", "established", "evidence_threshold"),
    ("hypothesis", "to_verify", "expired"), ("hypothesis", "to_verify", "dispute"), ("hypothesis", "to_verify", "exam_deviation"),
    ("established", "to_verify", "expired"), ("established", "to_verify", "dispute"), ("established", "to_verify", "exam_deviation"),
    ("to_verify", "established", "verification_supported"), ("to_verify", "refuted", "verification_refuted"),
    ("hypothesis", "withdrawn", "fact_corrected"), ("established", "withdrawn", "fact_corrected"),
    ("to_verify", "withdrawn", "fact_corrected"),
    ("hypothesis", "hypothesis", "revised"), ("established", "established", "revised"), ("to_verify", "to_verify", "revised"),
    ("hypothesis", "hypothesis", "visibility_changed"), ("established", "established", "visibility_changed"),
    ("to_verify", "to_verify", "visibility_changed"),
})


class InputError(ValueError):
    """掌握行或判断行不合形状：整批拒绝（不带内容，防止把数据写进日志）"""


@dataclass(frozen=True)
class MasteryRow:
    """一行 KGE 掌握估计（KGE-04 spec 第 13 节的形状；第二步落地后由 platform.mastery_estimate 的节点键行填充）"""
    basis_id: str                    # platform.mastery_estimate.id（判断的依据行）
    student_id: str
    subject: str
    node_id: str
    cognitive_level: str
    gate: str                        # platform.mastery_state
    p_mastery: float | None
    confidence: float
    evidence_ids: tuple[str, ...]
    algorithm_version: str           # platform.versioned_name，如 kge-mastery@0.1.0.syn
    computed_at: datetime
    next_retest_at: datetime | None = None

    def check(self) -> None:
        ok = (UUID.fullmatch(self.basis_id) and UUID.fullmatch(self.student_id) and NODE_ID.fullmatch(self.node_id)
              and self.cognitive_level in COGNITIVE_LEVELS and self.gate in GATES and 0 <= self.confidence <= 1
              and (self.p_mastery is None or 0 <= self.p_mastery <= 1) and self.evidence_ids
              and all(UUID.fullmatch(e) for e in self.evidence_ids) and VERSIONED_NAME.fullmatch(self.algorithm_version)
              and self.computed_at.tzinfo is not None
              and (self.next_retest_at is None or self.next_retest_at.tzinfo is not None))
        if not ok:
            raise InputError("mastery row has an invalid shape")


@dataclass(frozen=True)
class ClaimRow:
    """insight.claim 的一行（洞察服务视角）。to_verify_since：最近一次转为待验证的时间（取自 claim_history）"""
    id: str
    student_id: str
    subject: str | None
    target_type: str
    target_id: str
    template_key: str
    template_version: int
    value: Mapping[str, Any]
    confidence: float
    evidence_ids: tuple[str, ...]
    basis_kind: str | None
    basis_id: str | None
    source_type: str
    model_version: str | None
    status: str
    visibility: tuple[str, ...]
    created_at: datetime
    expires_at: datetime
    updated_at: datetime
    to_verify_since: datetime | None = None


@dataclass(frozen=True)
class Op:
    """一次写入。claim_id 为空 = 新建（fields 是全部列）；否则是修改（fields 只含要改的列）"""
    claim_id: str | None
    unit: tuple[str, ...]
    from_status: str | None
    to_status: str
    reason: str
    fields: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        f = {k: (v.isoformat() if isinstance(v, datetime) else list(v) if isinstance(v, tuple) else v)
             for k, v in sorted(self.fields.items())}
        return {"claim_id": self.claim_id, "unit": list(self.unit), "from": self.from_status, "to": self.to_status,
                "reason": self.reason, "fields": f}


@dataclass(frozen=True)
class TemplateInfo:
    key: str
    version: int
    max_visibility: tuple[str, ...]
    max_ttl: timedelta


def check_ops(ops: Iterable[Op]) -> None:
    """每一步都必须在白名单里（与 0010 同一张表）；不合即报错，不交给数据库兜底"""
    for op in ops:
        if (op.from_status, op.to_status, op.reason) not in ALLOWED:
            raise InputError(f"illegal transition {op.from_status}->{op.to_status} ({op.reason})")


def model_version_of(algorithm_version: str) -> str:
    return f"algo:{algorithm_version}"


def _visibility(policy: Policy, subject: str | None, tpl: TemplateInfo) -> tuple[str, ...]:
    want = {"agent"} | ({"student", "guardian"} if subject in policy.visible_subjects else set())
    got = tuple(a for a in AUDIENCE_ORDER if a in want and a in tpl.max_visibility)
    return got or tuple(a for a in AUDIENCE_ORDER if a in tpl.max_visibility)[:1]


def _expiry(now: datetime, days: int, tpl: TemplateInfo, retest: datetime | None) -> datetime:
    exp = now + min(timedelta(days=days), tpl.max_ttl)
    if retest is not None and now < retest < exp:
        exp = retest
    return exp


def _meets_threshold(policy: Policy, row: MasteryRow) -> bool:
    return (policy.signed and GATE_RANK[row.gate] >= GATE_RANK[policy.establish_min_gate]
            and row.confidence >= policy.establish_confidence_min and len(set(row.evidence_ids)) >= policy.establish_evidence_min)


def _evidence(row: MasteryRow) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for e in row.evidence_ids:
        seen.setdefault(e, None)
    ids = list(seen)
    return tuple(ids[-MAX_EVIDENCE:])


def _supports(category: str, claim_gate: str | None, row: MasteryRow) -> bool:
    if category == "node_transfer":
        return row.gate in STAR_GATES
    return claim_gate is not None and GATE_RANK.get(row.gate, -1) >= GATE_RANK.get(claim_gate, 99)


def _eligible(policy: Policy, category: str, row: MasteryRow) -> bool:
    if category == "node_transfer":
        return row.gate in STAR_GATES
    return row.gate in policy.mastery_gates


def unit_of(claim: ClaimRow, policy: Policy) -> tuple[str, ...] | None:
    cat = policy.category_of(claim.template_key)
    if cat not in NODE_CATEGORIES or claim.target_type != "node":
        return None
    level = claim.value.get("level")
    return (cat, claim.subject or "", claim.target_id, level if isinstance(level, str) else "")


def plan_node_claims(existing: Sequence[ClaimRow], rows: Sequence[MasteryRow], policy: Policy, now: datetime,
                     templates: Mapping[str, TemplateInfo]) -> list[Op]:
    """templates：已审核模板（按策略类别）；某一类的模板没有登记或未审核时该类不生成（新建与修改都不做，已有判断只会到期）"""
    if now.tzinfo is None:
        raise InputError("now must be timezone-aware")
    students = {r.student_id for r in rows} | {c.student_id for c in existing}
    if len(students) > 1:
        raise InputError("one student per plan")
    for r in rows:
        r.check()
    by_unit_row: dict[tuple[str, ...], MasteryRow] = {}
    for r in sorted(rows, key=lambda x: (x.subject, x.node_id, x.cognitive_level, x.basis_id)):
        for cat in NODE_CATEGORIES:
            if _eligible(policy, cat, r):
                u = (cat, r.subject, r.node_id, r.cognitive_level)
                if u in by_unit_row:
                    raise InputError("two current mastery rows for one unit")
                by_unit_row[u] = r
    # 同一单元的“行在，但这一类不再适用”（迁移判断在门槛降到迁移以下时）也要看到这一行
    any_row = {(r.subject, r.node_id, r.cognitive_level): r for r in rows}
    live: dict[tuple[str, ...], ClaimRow] = {}
    for c in sorted(existing, key=lambda x: (x.created_at, x.id)):
        u = unit_of(c, policy)
        if u is None or c.status not in LIVE:
            continue
        live.setdefault(u, c)  # 至多一条活着的；多出的不动（不应出现，store 有唯一性检查）
    ops: list[Op] = []
    for u in sorted(set(by_unit_row) | set(live)):
        cat = u[0]
        tpl = templates.get(cat)
        if tpl is None:
            continue
        c = live.get(u)
        row = by_unit_row.get(u) or any_row.get((u[1], u[2], u[3]))
        ops.extend(_plan_unit(u, cat, c, row, policy, now, tpl))
    check_ops(ops)
    return ops


def _plan_unit(u: tuple[str, ...], cat: str, c: ClaimRow | None, row: MasteryRow | None, policy: Policy, now: datetime,
               tpl: TemplateInfo) -> list[Op]:
    ops: list[Op] = []
    status = c.status if c else None
    since = c.to_verify_since if c else None
    # A. 到期 → 待验证
    if c is not None and status in ("hypothesis", "established") and c.expires_at <= now:
        ops.append(Op(c.id, u, status, "to_verify", "expired"))
        status, since = "to_verify", c.expires_at
    if c is not None:
        claim_gate = c.value.get("gate") if isinstance(c.value.get("gate"), str) else None
        new_evidence = row is not None and bool(set(row.evidence_ids) - set(c.evidence_ids))
        verified_window = (status == "to_verify" and row is not None and since is not None and row.computed_at > since
                           and new_evidence)
        corrected = row is None or bool(set(c.evidence_ids) - set(row.evidence_ids))
        if corrected:
            ops.append(Op(c.id, u, status, "withdrawn", "fact_corrected"))
            c, status = None, None
        elif not _supports(cat, claim_gate, row):
            if verified_window:
                ops.append(Op(c.id, u, status, "refuted", "verification_refuted"))
            else:
                ops.append(Op(c.id, u, status, "withdrawn", "fact_corrected"))  # 偏差：到期前退级（lessons）
            c, status = None, None
        else:
            assert row is not None
            ops.extend(_revise(u, cat, c, status, row, policy, now, tpl, verified_window))
            return ops
    if row is not None and _eligible(policy, cat, row):
        ops.append(_create(u, cat, row, policy, now, tpl))
    return ops


def _fields_from(row: MasteryRow) -> dict[str, Any]:
    return {"value": {"level": row.cognitive_level, "gate": row.gate}, "confidence": round(row.confidence, 3),
            "evidence_ids": _evidence(row), "basis_kind": "mastery_estimate", "basis_id": row.basis_id,
            "model_version": model_version_of(row.algorithm_version)}


def _create(u: tuple[str, ...], cat: str, row: MasteryRow, policy: Policy, now: datetime, tpl: TemplateInfo) -> Op:
    status = "established" if _meets_threshold(policy, row) else "hypothesis"
    fields = {"student_id": row.student_id, "subject": row.subject, "target_type": "node", "target_id": row.node_id,
              "template_key": tpl.key, "template_version": tpl.version, "source_type": "inferred",
              "visibility": _visibility(policy, row.subject, tpl),
              "expires_at": _expiry(now, policy.ttl_days[cat], tpl, row.next_retest_at), **_fields_from(row)}
    return Op(None, u, None, status, "created", fields)


def _revise(u: tuple[str, ...], cat: str, c: ClaimRow, status: str, row: MasteryRow, policy: Policy, now: datetime,
            tpl: TemplateInfo, verified_window: bool) -> list[Op]:
    ops: list[Op] = []
    want = _fields_from(row)
    changed = {k: v for k, v in want.items() if _differs(getattr(c, k), v)}
    new_status, reason = status, "revised"
    if status == "to_verify" and verified_window and _meets_threshold(policy, row):
        new_status, reason = "established", "verification_supported"
        changed["expires_at"] = _expiry(now, policy.ttl_days[cat], tpl, row.next_retest_at)  # 验证支持才续期
    elif status == "hypothesis" and _meets_threshold(policy, row):
        new_status, reason = "established", "evidence_threshold"
    if status in ("hypothesis", "established") and row.next_retest_at is not None and now < row.next_retest_at < c.expires_at \
            and reason != "verification_supported":
        changed["expires_at"] = row.next_retest_at  # 复测提前：只缩短，不延长
    if changed or new_status != status:
        ops.append(Op(c.id, u, status, new_status, reason, changed))
    want_vis = _visibility(policy, row.subject, tpl)
    if tuple(c.visibility) != want_vis:
        ops.append(Op(c.id, u, new_status, new_status, "visibility_changed", {"visibility": want_vis}))
    return ops


def _differs(old: Any, new: Any) -> bool:
    if isinstance(new, float) or isinstance(old, float):
        return old is None or new is None or abs(float(old) - float(new)) > 5e-4
    if isinstance(new, tuple):
        return tuple(old or ()) != new
    if isinstance(new, dict):
        return dict(old or {}) != new
    return old != new


def apply_ops(existing: Sequence[ClaimRow], ops: Sequence[Op], now: datetime, new_ids: Iterable[str]) -> list[ClaimRow]:
    """把操作应用到内存里的判断（测试与性质测试用：模拟 store 的写入，不连库）"""
    rows = {c.id: c for c in existing}
    ids = iter(new_ids)
    for op in ops:
        if op.claim_id is None:
            f = dict(op.fields)
            cid = next(ids)
            rows[cid] = ClaimRow(id=cid, student_id=f["student_id"], subject=f["subject"], target_type=f["target_type"],
                                 target_id=f["target_id"], template_key=f["template_key"], template_version=f["template_version"],
                                 value=dict(f["value"]), confidence=f["confidence"], evidence_ids=tuple(f["evidence_ids"]),
                                 basis_kind=f["basis_kind"], basis_id=f["basis_id"], source_type=f["source_type"],
                                 model_version=f["model_version"], status=op.to_status, visibility=tuple(f["visibility"]),
                                 created_at=now, expires_at=f["expires_at"], updated_at=now)
        else:
            c = rows[op.claim_id]
            upd: dict[str, Any] = {k: (tuple(v) if k in ("evidence_ids", "visibility") else v) for k, v in op.fields.items()}
            if op.to_status == "to_verify" and c.status != "to_verify":
                upd["to_verify_since"] = now
            rows[op.claim_id] = replace(c, status=op.to_status, updated_at=now, **upd)
    return sorted(rows.values(), key=lambda x: (x.created_at, x.id))
