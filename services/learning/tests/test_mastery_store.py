"""掌握 v0 第二步（KGE-04 第二步 PR 2）读取层与写入层的纯函数单测：不连库。

- 读取层：准入取更严者、地图版本解析（rename / merge 延续，split 重新判定，retire 撤销，没有版本不猜）、打标归属、Evidence 组装；
- 写入层：任务对账（保留、按复测结果关闭、撤销、新建、自述验证不碰）；
- 能力视图读时汇总（weight 加权、最低门槛、最早复测、不点亮）。
全部合成数据（规则 8）。库上的场景在 test_mastery_db.py。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from learning.mastery.evidence import Unit
from learning.mastery.gates import RetestOutcome, Task
from learning.mastery.recompute import REPLAY_REASONS, diff_tasks
from learning.mastery.store import (EvidenceRow, MapContext, Mapped, NodeRow, OpenTask, Tag, aggregate_skill_view, facts_of,
                                    map_row, resolve_node, stricter, to_evidence)

T = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)
V1, V2, V3 = "v1", "v2", "v3"
CTX = MapContext(
    subject="aya", current_id=V3,
    nodes={"MATH.A.B.N1": ("apply",), "MATH.A.B.N3R": ("apply",), "MATH.A.B.N67": ("apply",), "MATH.A.B.N4A": ("apply",)},
    chain=(V1, V2, V3),
    migrations={
        V2: {"MATH.A.B.N3": (("MATH.A.B.N3X", "rename"),), "MATH.A.B.N5": ((None, "retire"),),
             "MATH.A.B.N4": (("MATH.A.B.N4A", "split"), ("MATH.A.B.N4B", "split"))},
        V3: {"MATH.A.B.N3X": (("MATH.A.B.N3R", "rename"),), "MATH.A.B.N6": (("MATH.A.B.N67", "merge"),),
             "MATH.A.B.N7": (("MATH.A.B.N67", "merge"),), "MATH.A.B.N8": (("MATH.A.B.N1", "manual"),)},
    })


def test_stricter_admission():
    assert stricter("admitted", "admitted") == "admitted"
    assert stricter("admitted", "rejected") == "rejected"
    assert stricter("pending_review", "source_unverified") == "source_unverified"
    assert stricter("admitted", None) == "source_unverified"  # 题目未登记：不当作已准入


@pytest.mark.parametrize("version,node,expect", [
    (V3, "MATH.A.B.N1", ("MATH.A.B.N1", "ok")),
    (V1, "MATH.A.B.N1", ("MATH.A.B.N1", "ok")),           # 没有迁移记录：同 ID 延续
    (V1, "MATH.A.B.N3", ("MATH.A.B.N3R", "ok")),          # 两次改名按版本链依次延续
    (V2, "MATH.A.B.N3X", ("MATH.A.B.N3R", "ok")),
    (V2, "MATH.A.B.N6", ("MATH.A.B.N67", "ok")),          # 合并延续
    (V1, "MATH.A.B.N4", (None, "split")),                # 拆分：新节点重新判定
    (V1, "MATH.A.B.N5", (None, "retired")),
    (V1, "MATH.A.B.N8", (None, "split")),                # 人工迁移（manual）按拆分处理：新节点重新判定
    (V3, "MATH.A.B.N5", ("MATH.A.B.N5", "ok")),           # 迁移只看证据版本之后的版本
    (None, "MATH.A.B.N1", (None, "no_version")),          # 没有地图版本：不猜
    ("draft", "MATH.A.B.N1", (None, "unknown_version")),
])
def test_resolve_node(version, node, expect):
    assert resolve_node(CTX, version, node) == expect


def row(**kw) -> EvidenceRow:
    base = dict(attempt_id="att-1", evidence_id="ev-1", item_id="SYN-1", occurred_at=T - timedelta(days=3), recorded_at=T,
                hint_level=0, self_report=None, source_method="grade", evidence_kind="independent", evidence_level="A",
                map_version_id=V1, admission="admitted", grade_id="g-1", verdict="correct", grade_confidence=1.0,
                human_review_required=False, review_reasons=(), semantics="formal", in_diagnosis=False, error_category=None)
    base.update(kw)
    return EvidenceRow(**base)


def test_resolve_node_stops_at_current_version():
    back = MapContext("aya", V2, CTX.nodes, CTX.chain, CTX.migrations)  # 当前版本回退到 V2：V3 的迁移不算
    assert resolve_node(back, V1, "MATH.A.B.N3") == ("MATH.A.B.N3X", "ok")
    assert resolve_node(back, V2, "MATH.A.B.N6") == ("MATH.A.B.N6", "ok")
    assert resolve_node(back, V3, "MATH.A.B.N1") == (None, "unknown_version")


def test_map_row_by_evidence_version():
    tags = {(V1, "SYN-1"): Tag("MATH.A.B.N3", ("MATH.A.B.N6", "MATH.A.B.N4"), "apply", "fam-1", "approved"),
            (V3, "SYN-1"): Tag("MATH.A.B.N1", (), "apply", "fam-9", "approved")}
    m = map_row(row(), tags, CTX)
    # 按证据自身的版本（V1）解析，再映射到当前版本；次要节点同样映射，拆分的不归属
    assert m == Mapped("MATH.A.B.N3R", ("MATH.A.B.N67",), "apply", "fam-1")
    assert map_row(row(map_version_id=V3), tags, CTX).main == "MATH.A.B.N1"
    assert map_row(row(map_version_id=None), tags, CTX).main is None          # 没有版本不猜
    assert map_row(row(item_id="SYN-2"), tags, CTX).main is None              # 没有打标
    rejected = {(V1, "SYN-1"): Tag("MATH.A.B.N1", (), "apply", None, "rejected")}
    assert map_row(row(), rejected, CTX).main is None                          # 打标被驳回
    split = {(V1, "SYN-1"): Tag("MATH.A.B.N4", (), "apply", None, "approved")}
    assert map_row(row(), split, CTX).main is None


def test_to_evidence_fields():
    m = Mapped("MATH.A.B.N1", (), "apply", "fam-1")
    e = to_evidence(row(), m, "syn-solve")
    assert (e.evidence_id, e.attempt_id, e.recorded_at, e.occurred_at) == ("ev-1", "att-1", T, T - timedelta(days=3))
    assert (e.main_node_id, e.cognitive_level, e.family_id, e.item_type, e.tagged) == ("MATH.A.B.N1", "apply", "fam-1", "syn-solve", True)
    assert (e.has_grade, e.has_response_ref, e.source) == (True, True, "grade")
    assert to_evidence(row(grade_id=None, verdict=None), m, None).has_grade is False
    assert to_evidence(row(in_diagnosis=True), m, None).source == "diagnose"      # 诊断会话的判分按平台诊断来源
    assert to_evidence(row(source_method="diagnose"), m, None).source == "diagnose"
    assert to_evidence(row(), Mapped(None, (), None, None), None).tagged is False
    # 写库前自检用的事实：没有判分的按“待复核”处理（不合格）
    assert facts_of(row(grade_id=None)).human_review_required is True
    assert facts_of(row()).human_review_required is False


def ot(i: str, reason: str, rung: str | None, due: datetime) -> OpenTask:
    return OpenTask(i, reason, rung, due, None)


def test_diff_tasks_keep_close_cancel_create():
    d1 = T + timedelta(hours=2)
    existing = [ot("t-r0", "scheduled", "rung0", d1), ot("t-h", "hint_followup", None, T), ot("t-v", "verify_self_report", None, T)]
    # 本轮 rung0 通过（重放里有结果）→ 关闭并记作答；新的 rung1 新建；提示后变式不再需要 → 撤销；自述验证不碰
    retests = [RetestOutcome("rung0", "scheduled", d1, "passed", "ev-9")]
    plan = diff_tasks(existing, [Task("scheduled", "rung1", T + timedelta(days=2))], retests, {"ev-9": "att-9"})
    assert plan.close == [("t-r0", "passed", "att-9")]
    assert plan.cancel == ["t-h"]
    assert plan.create == [Task("scheduled", "rung1", T + timedelta(days=2))]
    # 同原因同轮次同到期：保留，不重建
    same = diff_tasks([ot("t-r0", "scheduled", "rung0", d1)], [Task("scheduled", "rung0", d1)], [], {})
    assert (same.close, same.cancel, same.create) == ([], [], [])
    # 排程变了、没有复测结果（退级、证据被撤销）→ 撤销旧任务
    moved = diff_tasks([ot("t-r0", "scheduled", "rung0", d1)], [Task("scheduled", "rung0", d1 + timedelta(hours=1))], [], {})
    assert moved.cancel == ["t-r0"] and len(moved.create) == 1
    # 失败也关闭（failed），并从失败时刻重排
    failed = diff_tasks([ot("t-r0", "scheduled", "rung0", d1)], [Task("scheduled", "rung0", T + timedelta(hours=5))],
                        [RetestOutcome("rung0", "scheduled", d1, "failed", "ev-3")], {"ev-3": "att-3"})
    assert failed.close == [("t-r0", "failed", "att-3")] and len(failed.create) == 1
    assert "verify_self_report" not in REPLAY_REASONS


def test_diff_tasks_keep_rounds():
    d1 = T + timedelta(hours=2)
    plan = diff_tasks([ot("t-r0", "scheduled", "rung0", d1), ot("t-d", "decay", None, T)], [], [], {}, keep_rounds=True)
    assert plan.cancel == ["t-d"]  # 记录原样保留时复测轮任务不动，其余照常对账


def test_aggregate_skill_view():
    rows = [NodeRow("N1", "understand", "stable", 0.8, T + timedelta(days=2)),
            NodeRow("N1", "apply", "execute", 0.6, None),
            NodeRow("N2", "apply", "transfer", 0.9, T + timedelta(days=9)),
            NodeRow("N9", "apply", "retain", 0.99, None)]  # 没挂接：不进任何能力点
    links = [("K-1", "N1", 1.0), ("K-1", "N2", 0.5), ("K-2", "N2", 1.0), ("K-3", "N8", 1.0)]
    levels = {"N1": ("understand", "apply"), "N2": ("apply",), "N8": ("apply",), "N9": ("apply",)}
    v = aggregate_skill_view(rows, links, levels)
    assert set(v) == {"K-1", "K-2"}                        # K-3 的挂接节点没有记录：不出现
    assert v["K-1"]["state"] == "execute"                   # 最低门槛（保守）
    assert v["K-1"]["p_mastery"] == pytest.approx((1.0 * 0.7 + 0.5 * 0.9) / 1.5)
    assert v["K-1"]["next_retest_at"] == T + timedelta(days=2)
    assert v["K-2"] == {"state": "transfer", "p_mastery": 0.9, "next_retest_at": T + timedelta(days=9)}
    assert not any("star_lit" in x for x in v.values())     # 能力视图不点亮
    with pytest.raises(ValueError):
        aggregate_skill_view(rows, links, levels, method="max")


def test_aggregate_skill_view_missing_level_is_unseen_and_invalid_units_dropped():
    rows = [NodeRow("N1", "apply", "retain", 0.9, None),
            NodeRow("N2", "understand", "retain", 0.95, None),     # 层次已从节点上删掉：不进汇总
            NodeRow("N3", "apply", "retain", 0.95, None)]          # 节点不在当前地图（类型变了、撤下）：不进汇总
    links = [("K-1", "N1", 1.0), ("K-2", "N2", 1.0), ("K-3", "N3", 1.0)]
    v = aggregate_skill_view(rows, links, {"N1": ("understand", "apply"), "N2": ("apply",)})
    # N1 的 understand 层次没有记录：按 unseen 计入门槛；p 只由有记录的层次算
    assert v == {"K-1": {"state": "unseen", "p_mastery": 0.9}}


def test_unit_selection_is_by_main_node_and_level():
    from learning.mastery.store import StudentEvidence
    rows = [row(evidence_id="e1", attempt_id="a1"), row(evidence_id="e2", attempt_id="a2"), row(evidence_id="e3", attempt_id="a3")]
    mapped = {"e1": Mapped("N1", (), "apply", None), "e2": Mapped("N2", ("N1",), "apply", None), "e3": Mapped("N1", (), "know", None)}
    se = StudentEvidence(rows, mapped, lambda s, i: None, "aya")
    assert {e.evidence_id for e in se.for_unit(Unit("N1", "apply"))} == {"e1", "e2"}  # 次要节点只为解释带进来（不计入）
    assert se.units() == {Unit("N1", "apply"), Unit("N2", "apply"), Unit("N1", "know")}
    assert se.units(["a2"]) == {Unit("N2", "apply")}                                    # 只按主节点
