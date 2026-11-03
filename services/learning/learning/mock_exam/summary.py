"""整卷汇总（PLT-18 spec 范围第 3 条）。纯函数：输入卷、已结束的会话、逐题判分结果、题目 → 节点快照、配置，可选采分点 → 节点快照
与题目登记的判分版本；不读库、不写证据。

判分结果只接受验证器 / Rubric 的结果（规则 3）。判分方式 grading 不另立词表，取再练题面登记的 practice.presenter.GRADING，
与插件契约 DimensionScore.judged_by 的对应：validator ↔ verifier、deterministic_rubric ↔ rubric_rule、model_rubric ↔ rubric_model
（第二步改为从 GradeResult 的维度推出，不再由调用方给）。每条必须带溯源五字段 evidence_ids、confidence、source_version、
model_version、human_review_required，缺任一字段（或为空）整卷拒绝（MISSING_PROVENANCE）；字段不合规整卷拒绝（GRADE_INVALID）。另外：
- 判分必须对应会话里该题最后一次作答的 answer_ref（重复作答取最后一次；对旧作答的判分拒绝，GRADE_STALE）；
- 未作答的题不能有判分（GRADE_FOR_UNANSWERED）；作答了的题必须有判分（GRADE_MISSING）；
- 卷面得分 = 判分得分 / 判分满分 × 卷面分值，四舍五入到 0.01（ROUND_HALF_UP）；判分满分 ≤ 0 拒绝；
- 判分得分为 null（契约 score.total 可为 null：维度不可用）的题不整卷拒绝：计 0 分、列入待复核（score_unavailable），总分暂定；
- 契约 score.calibrated = false 的题让输出带“未校准”标记（uncalibrated）并列出题号；
- 给了题目登记的判分版本（item_rubrics）时，source_version 里必须有一段 rubric: 或 verifier: 与之相同（RUBRIC_MISMATCH）。
输出只有数字、ID 与编码：得分、满分、各节得分、未作答题数、超时状态（timed_out）；human_review_required、置信度低于
review_confidence_below 或得分为 null 的题列入 pending_review，总分标 provisional。
按知识节点的得失分聚合：判分带采分点、且采分点 → 节点快照完整覆盖这道题时按采分点归到节点（basis = scoring_points）；
否则整题计入它映射到的每个节点（basis = whole_item，并列入 whole_item_ids），所以各节点相加不一定等于总分。
nodes 只是证据汇总，不得落库，也不得当作掌握度使用；不生成任何对学生的结论性陈述（规则 11）。报告文案、判断与对账另起。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from ..practice.presenter import GRADING
from .config import MockExamConfig
from .manifest import Paper
from .session import Session

GRADINGS = GRADING
PROVENANCE = ("evidence_ids", "confidence", "source_version", "model_version", "human_review_required")
REQUIRED = ("item_id", "answer_ref", "grading", "score", "max_score", "calibrated") + PROVENANCE
OPTIONAL = frozenset({"review_reasons", "scoring_points"})
POINT_KEYS = frozenset({"point_id", "achieved", "points_awarded", "evidence_ids"})
# 与插件契约 plugin-api v1.1 的 ModelVersion、SourceVersion、EvidenceId 同口径（packages/contracts 为准；测试逐字核对 pattern）
MODEL_VERSION_RE = re.compile(r"^(model:[a-z0-9\-]+/[A-Za-z0-9._\-]+@[A-Za-z0-9._\-]+|algo:[A-Za-z0-9._\-]+@[A-Za-z0-9._\-]+)$")
SOURCE_VERSION_RE = re.compile(r"^(rubric|verifier|prompt|items|algo|graph):[a-z0-9][a-z0-9\-]{0,47}@[A-Za-z0-9][A-Za-z0-9._\-]{0,31}"
                               r"(;(rubric|verifier|prompt|items|algo|graph):[a-z0-9][a-z0-9\-]{0,47}@[A-Za-z0-9][A-Za-z0-9._\-]{0,31}){0,7}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
REASON_RE = re.compile(r"^[a-z0-9_]{1,48}$")
POINT_RE = re.compile(r"^[a-z0-9_\-]{1,48}$")
FINISHED = ("submitted", "expired")
REVIEW_HUMAN = "human_review_required"
REVIEW_LOW_CONFIDENCE = "low_confidence"
REVIEW_SCORE_UNAVAILABLE = "score_unavailable"
BASIS_POINTS = "scoring_points"
BASIS_WHOLE = "whole_item"


class SummaryError(ValueError):
    def __init__(self, code: str, item_id: str | None = None) -> None:
        super().__init__(code if item_id is None else f"{code}: {item_id}")
        self.code = code
        self.item_id = item_id


@dataclass(frozen=True)
class _Grade:
    item_id: str
    answer_ref: str
    score: Decimal | None
    max_score: Decimal
    calibrated: bool
    evidence_ids: tuple[str, ...]
    confidence: float
    human_review_required: bool
    source_version: str
    points: tuple[tuple[str, Decimal | None], ...] | None


def num(c: int) -> int | float:
    """百分之一分 → JSON 数字（整数分写整数）。"""
    return c // 100 if c % 100 == 0 else c / 100


def _dec(v: Any) -> Decimal | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def scale(score: Decimal, max_score: Decimal, paper_cents: int) -> int:
    """卷面得分（百分之一分）= 判分得分 / 判分满分 × 卷面分值，四舍五入到 0.01（ROUND_HALF_UP）。"""
    return int((score * paper_cents / max_score).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _points(raw: object, iid: str) -> tuple[tuple[str, Decimal | None], ...]:
    if not isinstance(raw, list):
        raise SummaryError("GRADE_INVALID", iid)
    out: list[tuple[str, Decimal | None]] = []
    for p in raw:
        if not (isinstance(p, Mapping) and "point_id" in p and set(p) <= POINT_KEYS and isinstance(p["point_id"], str)
                and POINT_RE.fullmatch(p["point_id"])):
            raise SummaryError("GRADE_INVALID", iid)
        if "achieved" in p and p["achieved"] is not None and not isinstance(p["achieved"], bool):
            raise SummaryError("GRADE_INVALID", iid)
        aw = None
        if "points_awarded" in p:
            aw = _dec(p["points_awarded"])
            if aw is None or aw < 0:
                raise SummaryError("GRADE_INVALID", iid)
        out.append((p["point_id"], aw))
    if len({pid for pid, _ in out}) != len(out):
        raise SummaryError("GRADE_INVALID", iid)
    return tuple(out)


def _grade(g: object) -> _Grade:
    if not isinstance(g, Mapping):
        raise SummaryError("GRADE_INVALID")
    iid = g.get("item_id") if isinstance(g.get("item_id"), str) else None
    for k in PROVENANCE:
        if k not in g or g[k] is None:
            raise SummaryError("MISSING_PROVENANCE", iid)
    if any(k not in g for k in REQUIRED) or not set(g) <= set(REQUIRED) | OPTIONAL or iid is None:
        raise SummaryError("GRADE_INVALID", iid)
    if g["grading"] not in GRADINGS or not isinstance(g["answer_ref"], str) or not isinstance(g["calibrated"], bool):
        raise SummaryError("GRADE_INVALID", iid)
    ev = g["evidence_ids"]
    if not (isinstance(ev, list) and ev and all(isinstance(e, str) and UUID_RE.fullmatch(e) for e in ev) and len(set(ev)) == len(ev)):
        raise SummaryError("GRADE_INVALID", iid)
    c = g["confidence"]
    if isinstance(c, bool) or not isinstance(c, (int, float)) or not 0 <= c <= 1:
        raise SummaryError("GRADE_INVALID", iid)
    if not (isinstance(g["source_version"], str) and SOURCE_VERSION_RE.fullmatch(g["source_version"])):
        raise SummaryError("GRADE_INVALID", iid)
    if not (isinstance(g["model_version"], str) and MODEL_VERSION_RE.fullmatch(g["model_version"])):
        raise SummaryError("GRADE_INVALID", iid)
    if not isinstance(g["human_review_required"], bool):
        raise SummaryError("GRADE_INVALID", iid)
    reasons = g.get("review_reasons", [])
    if not (isinstance(reasons, list) and all(isinstance(r, str) and REASON_RE.fullmatch(r) for r in reasons)):
        raise SummaryError("GRADE_INVALID", iid)
    mx = _dec(g["max_score"])
    if mx is None or mx <= 0:
        raise SummaryError("GRADE_INVALID", iid)
    sc = None if g["score"] is None else _dec(g["score"])
    if g["score"] is not None and (sc is None or sc < 0 or sc > mx):
        raise SummaryError("GRADE_INVALID", iid)
    pts = _points(g["scoring_points"], iid) if "scoring_points" in g else None
    return _Grade(iid, g["answer_ref"], sc, mx, g["calibrated"], tuple(ev), float(c), g["human_review_required"],
                  g["source_version"], pts)


def _node_list(v: object, where: str) -> list[str]:
    if not isinstance(v, (list, tuple)) or not all(isinstance(n, str) and n for n in v):
        raise SummaryError("NODE_MAP_INVALID", where)
    return list(dict.fromkeys(v))


def _point_snapshot(raw: object, iid: str) -> dict[str, tuple[Decimal, list[str]]]:
    if not isinstance(raw, Mapping) or not raw:
        raise SummaryError("NODE_MAP_INVALID", iid)
    out: dict[str, tuple[Decimal, list[str]]] = {}
    for pid, row in raw.items():
        sc = _dec(row.get("score")) if isinstance(row, Mapping) else None
        if not isinstance(pid, str) or sc is None or sc <= 0:
            raise SummaryError("NODE_MAP_INVALID", iid)
        out[pid] = (sc, _node_list(row.get("node_ids"), iid))
    return out


def _rubric_ok(source_version: str, registered: str) -> bool:
    return any(seg.split(":", 1)[0] in ("rubric", "verifier") and seg.split(":", 1)[1] == registered
               for seg in source_version.split(";"))


def summarize(paper: Paper, session: Session, grades: Sequence[Mapping[str, Any]], item_nodes: Mapping[str, Sequence[str]],
              cfg: MockExamConfig, *, point_nodes: Mapping[str, Mapping[str, Any]] | None = None,
              item_rubrics: Mapping[str, str] | None = None) -> dict:
    if (session.paper_id, session.paper_version, session.subject) != (paper.paper_id, paper.version, paper.subject):
        raise SummaryError("PAPER_MISMATCH")
    if session.state not in FINISHED:
        raise SummaryError("NOT_FINISHED")
    if not isinstance(item_nodes, Mapping):
        raise SummaryError("NODE_MAP_INVALID")
    node_map = {iid: _node_list(v, iid) for iid, v in item_nodes.items()}
    by_item: dict[str, _Grade] = {}
    for raw in grades:
        g = _grade(raw)
        if g.item_id in by_item:
            raise SummaryError("GRADE_DUPLICATE", g.item_id)
        item = paper.item(g.item_id)
        if item is None:
            raise SummaryError("ITEM_NOT_IN_PAPER", g.item_id)
        a = session.answer_of(g.item_id)
        if a is None:
            raise SummaryError("GRADE_FOR_UNANSWERED", g.item_id)
        if g.answer_ref != a.answer_ref:
            raise SummaryError("GRADE_STALE", g.item_id)
        if item_rubrics is not None and g.item_id in item_rubrics and not _rubric_ok(g.source_version, item_rubrics[g.item_id]):
            raise SummaryError("RUBRIC_MISMATCH", g.item_id)
        by_item[g.item_id] = g
    for a in session.answers:
        if a.item_id not in by_item:
            raise SummaryError("GRADE_MISSING", a.item_id)

    pending: list[dict] = []
    uncalibrated: list[str] = []
    sections: list[dict] = []
    nodes: dict[str, dict[str, Any]] = {}
    unmapped: list[str] = []
    whole: list[str] = []
    total = 0
    unanswered = 0

    def credit(n: str, iid: str, earned_c: int, possible_c: int, basis: str) -> None:
        acc = nodes.setdefault(n, {"earned": 0, "possible": 0, "items": {}})
        acc["earned"] += earned_c
        acc["possible"] += possible_c
        acc["items"].setdefault(iid, basis)

    for sec in paper.sections:
        got = answered = 0
        for iid in sec.item_ids:
            item = paper.item(iid)
            assert item is not None
            g = by_item.get(iid)
            earned = 0
            if g is None:
                unanswered += 1
            else:
                answered += 1
                if g.score is not None:
                    earned = scale(g.score, g.max_score, item.paper_cents)
                if not g.calibrated:
                    uncalibrated.append(iid)
                reasons = ([REVIEW_HUMAN] if g.human_review_required else []) + \
                          ([REVIEW_LOW_CONFIDENCE] if g.confidence < cfg.review_confidence_below else []) + \
                          ([REVIEW_SCORE_UNAVAILABLE] if g.score is None else [])
                if reasons:
                    pending.append({"item_id": iid, "reasons": reasons})
            got += earned
            snap = None
            if g is not None and g.points is not None and point_nodes is not None and iid in point_nodes:
                snap = _point_snapshot(point_nodes[iid], iid)
                covered = ({pid for pid, _ in g.points} == set(snap) and all(aw is not None for _, aw in g.points)
                           and sum(sc for sc, _ in snap.values()) == g.max_score)
                if not covered:
                    snap = None
            if snap is not None and g is not None and g.points is not None:
                for pid, aw in g.points:
                    sc, ns = snap[pid]
                    for n in ns:
                        credit(n, iid, scale(aw or Decimal(0), g.max_score, item.paper_cents),
                               scale(sc, g.max_score, item.paper_cents), BASIS_POINTS)
                continue
            mapped = node_map.get(iid, [])
            if not mapped:
                unmapped.append(iid)
                continue
            whole.append(iid)
            for n in mapped:
                credit(n, iid, earned, item.paper_cents, BASIS_WHOLE)
        total += got
        sections.append({"name": sec.name, "score": num(got), "full_score": num(sec.full_cents), "answered": answered,
                         "unanswered": len(sec.item_ids) - answered})
    evidence = sorted({e for g in by_item.values() for e in g.evidence_ids})
    return {
        "paper_id": paper.paper_id, "paper_version": paper.version, "subject": paper.subject, "session_id": session.session_id,
        "state": session.state, "timed_out": session.state == "expired",
        "score": num(total), "full_score": num(paper.full_cents), "provisional": bool(pending),
        "uncalibrated": bool(uncalibrated), "uncalibrated_item_ids": uncalibrated,
        "sections": sections, "answered_count": len(by_item), "unanswered_count": unanswered,
        "pending_review": pending,
        "nodes": [{"node_id": n, "earned": num(v["earned"]), "lost": num(v["possible"] - v["earned"]), "possible": num(v["possible"]),
                   "items": [{"item_id": i, "basis": b} for i, b in v["items"].items()]} for n, v in sorted(nodes.items())],
        "whole_item_ids": whole, "unmapped_item_ids": unmapped, "evidence_ids": evidence,
    }
