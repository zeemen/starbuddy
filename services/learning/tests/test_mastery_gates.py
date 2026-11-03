"""五道门槛重放状态机、复测调度与退级、星亮、衰减、写入判定与写库前自检、解释（spec 第 4–9、13 节）。"""
from __future__ import annotations

import json
import re

import pytest

from learning.mastery import (EvidenceFacts, InvariantViolation, StoredEstimate, Unit, explain_gates, precommit_check, replay,
                              star_lit, write_decision)
from learning.mastery import evidence as E
from learning.mastery.config import parse_config
from test_mastery_support import (DAY, HOUR, MIN, NODE, T0, UNIT, ev, normal_path, signed_cfg, signed_doc, unsigned_cfg,
                                  with_)

S = signed_cfg()
U = unsigned_cfg()
RUNG0, RUNG1, RUNG2 = 2 * HOUR, 2 * DAY, 9 * DAY  # 合成配置里的间隔


def gates_of(rep):
    return [t.to_gate for t in rep.transitions]


def reasons(rep, eid):
    return [r for e, rs in rep.excluded if e == eid for r in rs]


def task_reasons(rep):
    return sorted(t.reason for t in rep.tasks)


def cond(rep, code):
    return next(c for c in rep.conditions if c.code == code)


def facts_for(evs):
    return {e.evidence_id: EvidenceFacts(e.evidence_level, e.admission, e.human_review_required, e.semantics) for e in evs}


# ---- 正常路径与复测调度 ----

def test_normal_path_reaches_retain_step_by_step():
    evs = normal_path()
    seen = []
    for i in range(1, len(evs) + 1):
        seen.append(replay(evs[:i], UNIT, S, evs[i - 1].recorded_at).result.state)
    assert seen == ["understand", "execute", "stable", "transfer", "retain"]
    rep = replay(evs, UNIT, S, T0 + 14 * DAY)
    assert gates_of(rep) == ["understand", "execute", "stable", "transfer", "retain"]
    assert rep.result.next_retest_at is None and rep.tasks == []       # 到达保持后不再排复测（#14）
    assert [o.outcome for o in rep.retests] == ["passed", "passed", "passed"]
    precommit_check(rep.result, facts_for(evs))


def test_retest_due_times_equal_arrival_plus_interval():
    evs = normal_path()
    r2 = replay(evs[:2], UNIT, S, evs[1].recorded_at)
    assert r2.result.next_retest_at == evs[1].recorded_at + RUNG0
    assert [(t.reason, t.rung, t.due_at) for t in r2.tasks] == [("scheduled", "rung0", evs[1].recorded_at + RUNG0)]
    r3 = replay(evs[:3], UNIT, S, evs[2].recorded_at)
    assert r3.result.next_retest_at == evs[2].recorded_at + RUNG1
    r4 = replay(evs[:4], UNIT, S, evs[3].recorded_at)
    assert r4.result.state == "transfer" and r4.result.next_retest_at == evs[3].recorded_at + RUNG2
    # 重算多少次都一样：到期时间只取决于证据
    assert replay(evs[:4], UNIT, S, T0 + 30 * DAY).result.next_retest_at == r4.result.next_retest_at


def test_retest_too_early_does_not_count():
    evs = normal_path()
    early = ev(evs[1].recorded_at + HOUR, "fam-early", item_type="syn-proof", eid="syn-early")  # rung0 到期前
    rep = replay(evs[:2] + [early], UNIT, S, early.recorded_at)
    assert rep.result.state == "execute"
    assert ("syn-early", E.RETEST_TOO_EARLY) in rep.annotations
    assert rep.retests == []


def test_forged_client_time_does_not_pass_interval():
    evs = normal_path()
    forged = ev(evs[1].recorded_at + HOUR, "fam-forged", item_type="syn-proof", eid="syn-forged",
                occurred_at=evs[1].recorded_at + 5 * DAY)
    rep = replay(evs[:2] + [forged], UNIT, S, forged.recorded_at)
    assert rep.result.state == "execute" and ("syn-forged", E.RETEST_TOO_EARLY) in rep.annotations


def test_retest_fail_then_other_item_correct_still_failed():
    evs = normal_path()
    t_fail = evs[1].recorded_at + 3 * HOUR
    fail = ev(t_fail, "fam-f1", item_type="syn-proof", verdict="incorrect", eid="syn-fail")
    retry = ev(t_fail + 10 * MIN, "fam-f2", item_type="syn-proof", eid="syn-retry")
    rep = replay(evs[:2] + [fail, retry], UNIT, S, retry.recorded_at)
    assert [(o.rung, o.outcome, o.evidence_id) for o in rep.retests] == [("rung0", "failed", "syn-fail")]
    assert rep.result.state == "execute"
    assert rep.result.next_retest_at == t_fail + RUNG0             # 从失败时刻重排
    assert ("syn-retry", E.RETEST_TOO_EARLY) in rep.annotations


def test_rung1_failure_keeps_stable_and_reschedules():
    evs = normal_path()[:3]
    t_fail = evs[2].recorded_at + 3 * DAY
    rep = replay(evs + [ev(t_fail, "fam-x", verdict="incorrect")], UNIT, S, t_fail)
    assert rep.result.state == "stable" and rep.result.next_retest_at == t_fail + RUNG1


def test_rung2_failure_demotes_lit_star_to_stable():
    evs = normal_path()[:4]
    t_fail = evs[3].recorded_at + 10 * DAY
    rep = replay(evs + [ev(t_fail, "fam-x", verdict="incorrect")], UNIT, S, t_fail)
    assert rep.result.state == "stable"
    assert rep.transitions[-1].cause == "retest_failed" and rep.transitions[-1].from_gate == "transfer"
    assert rep.result.next_retest_at == t_fail + RUNG1                  # 重新走第二轮 → 第三轮
    assert cond(rep, "transfer.rung1_passed").met is False
    # 重新走一遍能回到迁移
    t2 = t_fail + RUNG1 + HOUR
    rep2 = replay(evs + [ev(t_fail, "fam-x", verdict="incorrect"), ev(t2, "fam-y", kind="transfer")], UNIT, S, t2)
    assert rep2.result.state == "transfer"


def test_retain_star_early_retest_on_ordinary_mistakes_then_demote():
    evs = normal_path()
    t = T0 + 20 * DAY
    wrongs = [ev(t + i * MIN, f"fam-w{i}", verdict="incorrect", semantics="practice", eid=f"syn-w{i}") for i in range(12)]
    # 普通练习做错不直接熄灭（#13）：p 跌破稳定层下限后把第三轮复测提前到当下
    rep = replay(evs + wrongs, UNIT, S, t + HOUR)
    assert rep.result.p_mastery < S["gates"]["stable"]["p_floor"]
    early = [o for o in rep.retests if o.reason == "early"]
    # 提前复测只认严格证据：practice 语义的作答不能当第三轮，所以还没有结果
    assert early == [] and rep.result.state == "retain"
    assert [(x.reason, x.rung) for x in rep.tasks] == [("early", "rung2")]
    t_retest = t + 2 * HOUR
    rep2 = replay(evs + wrongs + [ev(t_retest, "fam-r", verdict="incorrect")], UNIT, S, t_retest)
    assert rep2.result.state == "stable" and rep2.retests[-1].reason == "early"
    assert rep2.result.next_retest_at == t_retest + RUNG1


def test_transfer_star_early_retest_moves_due_forward():
    evs = normal_path()[:4]
    t = evs[3].recorded_at + HOUR
    wrongs = [ev(t + i * MIN, f"fam-w{i}", verdict="incorrect", eid=f"syn-tw{i}") for i in range(12)]
    rep = replay(evs + wrongs, UNIT, S, t + HOUR)
    # 第一道严格的做错在 rung2 到期前，只是提前了复测；随后的严格做错就是提前复测的结果
    assert rep.result.state == "stable"
    assert any(o.reason == "early" and o.outcome == "failed" for o in rep.retests)


def test_missed_retest_does_not_demote():
    evs = normal_path()[:3]
    rep = replay(evs, UNIT, S, T0 + 200 * DAY)
    assert rep.result.state == "stable"
    assert rep.result.next_retest_at == evs[2].recorded_at + RUNG1     # 任务一直开着（#15）
    assert "scheduled" in task_reasons(rep)


# ---- 等级、准入、签字、待复核 ----

def _lit_path(**kw):
    return [with_(e, **kw) for e in normal_path()]


@pytest.mark.parametrize("kw,cap", [
    ({"evidence_level": "B"}, E.LEVEL_B_CAPPED),
    ({"admission": "pending_review"}, E.NOT_ADMITTED),
    ({"semantics": "practice"}, E.NOT_FORMAL),
    ({"evidence_kind": None, "evidence_level": None}, E.LEGACY_NO_KIND),
])
def test_capped_evidence_stops_at_stable(kw, cap):
    evs = _lit_path(**kw)
    if cap == E.LEGACY_NO_KIND:
        evs = [with_(e, source="diagnose") for e in evs]  # 旧证据：提示等级 0，按保守方向
    rep = replay(evs, UNIT, S, T0 + 14 * DAY)
    assert rep.result.state == "stable"
    ex = explain_gates(rep)
    assert all(cap in c["caps"] for c in ex["capped"]) and len(ex["capped"]) == len(evs)


def test_b_level_only_gets_verify_in_platform_task():
    rep = replay(_lit_path(evidence_level="B"), UNIT, S, T0 + 14 * DAY)
    assert rep.result.state == "stable"
    assert "verify_in_platform" in task_reasons(rep)
    # 全是 A 级时没有这条任务
    assert "verify_in_platform" not in task_reasons(replay(normal_path(), UNIT, S, T0 + 14 * DAY))


def test_unsigned_config_never_lights():
    rep = replay(normal_path(), UNIT, U, T0 + 14 * DAY)
    assert rep.result.state == "stable"
    c = cond(rep, "transfer.config_signed")
    assert c.met is False and c.current == 0
    assert all(cond(rep, code).met for code in ("transfer.rung1_passed", "transfer.transfer_evidence_min",
                                                "transfer.strict_families_min", "transfer.p_floor"))
    assert "verify_in_platform" not in task_reasons(rep)


def test_pending_review_does_not_move_gates():
    evs = normal_path()
    flagged = [with_(e, human_review_required=True, review_reasons=("spot_check",)) if i >= 1 else e
               for i, e in enumerate(evs)]
    rep = replay(flagged, UNIT, S, T0 + 14 * DAY)
    assert rep.result.state == "understand"
    base = replay(evs[:1], UNIT, S, T0 + 14 * DAY)
    assert rep.result.p_mastery == base.result.p_mastery
    assert all(reasons(rep, e.evidence_id) == [E.PENDING_REVIEW] for e in flagged[1:])
    # 复核结案、判分确认后重放：按确认后的判分计入
    assert replay(evs, UNIT, S, T0 + 14 * DAY).result.state == "retain"


def test_bkt_floor_blocks_promotion_after_recent_mistakes():
    # 规则负责可解释，BKT 只做下限：连续做错后，两族做对也挡住“执行”
    evs = [ev(T0, "fam-a")] + [ev(T0 + (i + 1) * MIN, f"fam-w{i}", verdict="incorrect") for i in range(5)] \
        + [ev(T0 + 10 * MIN, "fam-b")]
    rep = replay(evs, UNIT, S, T0 + HOUR)
    assert rep.result.state == "understand"
    assert cond(rep, "execute.families_min").met and not cond(rep, "execute.p_floor").met
    more = evs + [ev(T0 + 20 * MIN + i * MIN, f"fam-c{i}") for i in range(3)]
    assert replay(more, UNIT, S, T0 + HOUR).result.state == "execute"


def test_transfer_requires_transfer_kind_evidence():
    evs = [with_(e, evidence_kind="independent") for e in normal_path()]
    rep = replay(evs, UNIT, S, T0 + 14 * DAY)
    assert rep.result.state == "stable" and cond(rep, "transfer.transfer_evidence_min").met is False


def test_single_item_type_group_stops_at_execute():
    evs = [with_(e, item_type="syn-solve") for e in normal_path()]
    rep = replay(evs, UNIT, S, T0 + 14 * DAY)
    assert rep.result.state == "execute" and cond(rep, "stable.item_type_groups_min").met is False


def test_unknown_item_type_counts_for_no_group():
    evs = [with_(e, item_type=None) for e in normal_path()]
    assert replay(evs, UNIT, S, T0 + 14 * DAY).result.state == "execute"


def test_hint_heavy_all_the_way_keeps_p_and_gate_and_asks_followup():
    evs = [ev(T0 + i * HOUR, f"fam-{i}", kind="hinted", hint=5) for i in range(6)]
    rep = replay(evs, UNIT, S, T0 + DAY)
    assert rep.result.state is None and rep.result.p_mastery == S["bkt"]["p_init"]
    assert task_reasons(rep) == ["hint_followup"]
    # 之后无提示变式做对：hint_followup 关闭
    rep2 = replay(evs + [ev(T0 + DAY, "fam-new")], UNIT, S, T0 + DAY)
    assert "hint_followup" not in task_reasons(rep2)


def test_no_counted_evidence_writes_nothing():
    rep = replay([ev(T0, "f", human_review_required=True)], UNIT, S, T0)
    assert rep.result.state is None and rep.result.evidence_ids == ()
    assert write_decision(None, rep.result, S) == (False, ("no_counted_evidence",))


# ---- 衰减 ----

def test_decay_lowers_only_confidence_and_opens_one_task():
    evs = normal_path()
    last = evs[-1].recorded_at
    prev = None
    decay_tasks = []
    for d in range(0, 400, 7):
        rep = replay(evs, UNIT, S, last + d * DAY)
        assert rep.result.state == "retain"                              # 已亮的星不熄灭
        assert rep.result.p_mastery == replay(evs, UNIT, S, last).result.p_mastery
        if prev is not None:
            assert rep.result.confidence <= prev
        prev = rep.result.confidence
        decay_tasks.append([t for t in rep.tasks if t.reason == "decay"])
    assert prev < S["review_confidence_floor"]
    assert decay_tasks[0] == [] and len(decay_tasks[-1]) == 1
    # 到期时间只取决于证据：任何 now 下都相同
    dues = {ts[0].due_at for ts in decay_tasks if ts}
    assert len(dues) == 1
    due = dues.pop()
    assert replay(evs, UNIT, S, due - MIN).result.confidence >= S["review_confidence_floor"]
    assert not [t for t in replay(evs, UNIT, S, due - HOUR).tasks if t.reason == "decay"]


def test_no_decay_within_grace():
    evs = normal_path()
    last = evs[-1].recorded_at
    a = replay(evs, UNIT, S, last)
    b = replay(evs, UNIT, S, last + 9 * DAY)  # 合成宽限期 10 天
    assert a.result.confidence == b.result.confidence == a.confidence_raw


# ---- 写入判定、写库前自检 ----

def _stored(r):
    return StoredEstimate(r.state, r.p_mastery, r.confidence, r.next_retest_at, r.algorithm_version)


def test_write_decision():
    evs = normal_path()
    r = replay(evs[:3], UNIT, S, evs[2].recorded_at).result
    assert write_decision(None, r, S) == (True, ("first",))
    assert write_decision(_stored(r), r, S) == (False, ())
    # 同样输入重算两次，第二次不写
    r_again = replay(evs[:3], UNIT, S, evs[2].recorded_at).result
    assert write_decision(_stored(r), r_again, S)[0] is False
    r4 = replay(evs[:4], UNIT, S, evs[3].recorded_at).result
    ok, why = write_decision(_stored(r), r4, S)
    assert ok and "state" in why and "next_retest_at" in why
    # 小于阈值的衰减不写；超过阈值才写
    th = S["write_threshold"]["confidence"]
    tiny = type(r)(**{**r.__dict__, "confidence": r.confidence - th / 2})
    assert write_decision(_stored(r), tiny, S)[0] is False
    big = type(r)(**{**r.__dict__, "confidence": r.confidence - th * 2})
    assert write_decision(_stored(r), big, S) == (True, ("confidence",))
    pbig = type(r)(**{**r.__dict__, "p_mastery": r.p_mastery + S["write_threshold"]["p_mastery"] * 2})
    assert write_decision(_stored(r), pbig, S) == (True, ("p_mastery",))
    # 算法版本（含配置指纹）变化也写
    d = signed_doc()
    d["confidence_formula"]["k"] = 2.5
    other = replay(evs[:3], UNIT, parse_config(d, env="test"), evs[2].recorded_at).result
    assert "algorithm_version" in write_decision(_stored(r), other, S)[1]


def test_lit_record_lists_only_strict_evidence():
    evs = normal_path()
    extra_b = ev(T0 + 4 * DAY, "fam-bb", level="B", eid="syn-extra-b")
    rep = replay(evs + [extra_b], UNIT, S, T0 + 14 * DAY)
    assert rep.result.state == "retain"
    assert "syn-extra-b" not in rep.result.evidence_ids
    assert "syn-extra-b" in explain_gates(rep)["supporting_evidence_ids"]
    precommit_check(rep.result, facts_for(evs + [extra_b]))


@pytest.mark.parametrize("field,value", [("evidence_level", "B"), ("admission", "rejected"),
                                         ("human_review_required", True), ("semantics", "practice")])
def test_precommit_rejects_non_qualifying(field, value):
    evs = normal_path()
    rep = replay(evs, UNIT, S, T0 + 14 * DAY)
    facts = facts_for(evs)
    eid = rep.result.evidence_ids[0]
    facts[eid] = EvidenceFacts(**{**facts[eid].__dict__, field: value})  # 例如题目后来被撤销准入
    with pytest.raises(InvariantViolation):
        precommit_check(rep.result, facts)
    with pytest.raises(InvariantViolation):
        precommit_check(rep.result, {})


def test_admission_revoked_replay_falls_back_to_stable():
    evs = normal_path()
    revoked = [with_(e, admission="rejected") if e.family_id == "fam-d" else e for e in evs]
    rep = replay(revoked, UNIT, S, T0 + 14 * DAY)
    assert rep.result.state == "stable"


# ---- 星亮 ----

def test_star_lit_rules():
    levels = ["know", "apply"]
    assert star_lit(NODE, levels, {"apply": "transfer", "know": "stable"}, S)
    assert not star_lit(NODE, levels, {"apply": "stable", "know": "retain"}, S)   # 默认看最高层次
    d = signed_doc()
    d["star_level_rule"]["node_levels"] = {NODE: "know"}
    cfg = parse_config(d, env="test")
    assert star_lit(NODE, levels, {"apply": "stable", "know": "retain"}, cfg)
    assert not star_lit(NODE, ["know"], {"know": "retain"}, parse_config({**d, "star_level_rule": {"mode": "star_level", "node_levels": {NODE: "apply"}}}, env="test"))
    d["star_level_rule"] = {"mode": "all_levels", "node_levels": {}}
    cfg2 = parse_config(d, env="test")
    assert not star_lit(NODE, levels, {"apply": "transfer", "know": "stable"}, cfg2)
    assert star_lit(NODE, levels, {"apply": "transfer", "know": "retain"}, cfg2)
    assert not star_lit(NODE, [], {}, S)


def test_star_basis_level():
    """决定星态的层次（星图 v1 的 state 与 basis_id 取这一层；star_lit 与它同一份口径）。"""
    from learning.mastery.gates import star_basis_level
    levels = ["know", "understand", "apply"]
    # star_level：缺省取最高层次，与记录无关
    assert star_basis_level(NODE, levels, {}, S) == "apply"
    assert star_basis_level(NODE, levels, {"know": "retain"}, S) == "apply"
    d = signed_doc()
    d["star_level_rule"]["node_levels"] = {NODE: "understand"}
    assert star_basis_level(NODE, levels, {}, parse_config(d, env="test")) == "understand"
    # 配置的层次不在节点层次里：没有决定层次（星不亮、星态 unseen）
    d["star_level_rule"]["node_levels"] = {NODE: "synthesize"}
    assert star_basis_level(NODE, levels, {"apply": "retain"}, parse_config(d, env="test")) is None
    assert star_basis_level(NODE, [], {}, S) is None
    # all_levels：门槛最低的层次；同门槛取低层次；全亮时取最低的那一个亮着的层次
    d["star_level_rule"] = {"mode": "all_levels", "node_levels": {}}
    al = parse_config(d, env="test")
    assert star_basis_level(NODE, levels, {"know": "retain", "understand": "stable", "apply": "transfer"}, al) == "understand"
    assert star_basis_level(NODE, levels, {"know": "transfer", "understand": "transfer", "apply": "retain"}, al) == "know"
    assert star_basis_level(NODE, levels, {"know": "retain", "understand": "transfer", "apply": "transfer"}, al) == "understand"
    assert star_basis_level(NODE, levels, {"know": "retain", "apply": "retain"}, al) == "understand"   # 没有记录 = unseen，最低
    assert star_basis_level(NODE, levels, {"know": "execute", "understand": "execute", "apply": "execute"}, al) == "know"
    # star_lit 与它一致：决定层次的门槛到迁移及以上
    for gates in ({"know": "retain", "understand": "transfer", "apply": "transfer"}, {"know": "retain", "apply": "retain"}):
        lv = star_basis_level(NODE, levels, gates, al)
        assert star_lit(NODE, levels, gates, al) == (gates.get(lv) in ("transfer", "retain"))


# ---- 解释 ----

_SENTENCE = re.compile(r"[一-鿿]")


def _strings(x):
    if isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from _strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from _strings(v)
    elif isinstance(x, str):
        yield x


@pytest.mark.parametrize("scenario", ["normal", "early", "b_only", "spam", "review", "unsigned"])
def test_explain_codes_numbers_and_ids_only(scenario):
    evs = normal_path()
    cfg = S
    if scenario == "early":
        evs = evs[:2] + [ev(evs[1].recorded_at + HOUR, "fam-early", item_type="syn-proof")]
    elif scenario == "b_only":
        evs = _lit_path(evidence_level="B")
    elif scenario == "spam":
        evs = [ev(T0 + i * DAY, "fam-same", item_id="syn-same") for i in range(6)]
    elif scenario == "review":
        evs = [with_(e, human_review_required=True) for e in evs]
    elif scenario == "unsigned":
        cfg = U
    rep = replay(evs, UNIT, cfg, T0 + 30 * DAY)
    out = explain_gates(rep)
    assert not any(_SENTENCE.search(s) for s in _strings(out))        # 不输出自然语言
    known = set(out["evidence_ids"]) | set(out["supporting_evidence_ids"]) | {x["evidence_id"] for x in out["excluded"]}
    for c in out["conditions"]:
        assert set(c["evidence_ids"]) <= known
        assert isinstance(c["current"], (int, float)) and isinstance(c["required"], (int, float))
    assert {e.evidence_id for e in evs} == known
    json.dumps(out)
    if scenario == "spam":
        assert out["gate"] == "understand"
        assert [x["reasons"] for x in out["excluded"]] == [[E.REPEAT_FAMILY]] * 5
    if scenario == "review":
        assert all(x["reasons"] == [E.PENDING_REVIEW] for x in out["excluded"])
    if scenario == "early":
        assert any(a["code"] == E.RETEST_TOO_EARLY for a in out["annotations"])
    if scenario == "unsigned":
        assert out["config_signed"] is False and out["calibrated"] is False


def test_naive_datetime_rejected():
    from datetime import datetime as dt
    with pytest.raises(ValueError):
        replay([ev(dt(2031, 1, 1), "f")], UNIT, S, T0)
    with pytest.raises(ValueError):
        replay([], UNIT, S, dt(2031, 1, 1))


def test_other_units_evidence_is_ignored_for_this_unit():
    evs = normal_path()
    rep = replay(evs, Unit("syn-node-002", "apply"), S, T0 + 14 * DAY)
    assert rep.result.state is None and all(r == (E.OTHER_UNIT,) for _, r in rep.excluded)
