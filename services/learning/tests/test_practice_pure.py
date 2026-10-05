"""错题再练 PR 1 纯函数单测（specs/practice-loop-stage1/spec.md 第 1、2、7 节与票据）。不连库、不发 HTTP；全部合成数据。"""
from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from learning.practice import config as C
from learning.practice import queue as Q
from learning.practice import select as S
from learning.practice import ticket as T

T0 = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)
H, D = timedelta(hours=1), timedelta(days=1)
U1 = Q.Unit("MATH.SYN.T1.N1", "apply")
U2 = Q.Unit("MATH.SYN.T1.N2", "apply")
CFG = C.parse(C.SYNTHETIC, env="test")
STUDENT = "5b8e1c52-0000-4000-8000-0000000000aa"
SECRET = "synthetic-practice-ticket-secret-000000001"
_n = [0]


def att(t: datetime, item: str = "I1", verdict: str = "incorrect", *, unit: Q.Unit | None = U1, hint: int | None = 0,
        level: str | None = "A", channel: str | None = "keypad", review: bool = False, reasons: tuple[str, ...] = (),
        self_report: str | None = None, diag: bool = False, graded: bool = True) -> Q.AttemptFact:
    _n[0] += 1
    return Q.AttemptFact(attempt_id=f"00000000-0000-4000-8000-{_n[0]:012d}", item_id=item, recorded_at=t,
                         verdict=verdict if graded else None, human_review_required=review, evidence_level=level, channel=channel,
                         node_id=unit.node_id if unit else None, cognitive_level=unit.cognitive_level if unit else None,
                         hint_level=hint, self_report=self_report, review_reasons=reasons, in_diagnosis=diag, has_grade=graded)


def ok(t, item="I2", **kw):
    return att(t, item, "correct", **kw)


def one(entries: list[Q.Entry], unit: Q.Unit = U1) -> Q.Entry:
    found = [e for e in entries if e.unit == unit]
    assert len(found) == 1, entries
    return found[0]


def build(attempts, now, hint_tasks=(), retest=(), cfg=CFG, **kw):
    return Q.build(attempts, hint_tasks, retest, cfg, now, **kw)


# ---------- 配置 ----------
def test_synthetic_config_only_in_test_and_dev():
    for env in ("test", "dev"):
        cfg = C.load("synthetic", env=env)
        assert cfg.synthetic and not cfg.signed
        assert cfg.round_delay_hours == (0, 48, 120) and cfg.max_rounds == 3 and cfg.subjects_enabled == ("aya", "zhixing")
    for env in ("pilot", "prod", ""):
        with pytest.raises(C.PracticeConfigError):
            C.load("synthetic", env=env)


def signed_doc(**over):
    d = dict(C.SYNTHETIC, synthetic=False, signed_by="synthetic-signer", signed_at="2026-10-01T00:00:00+08:00")
    d.update(over)
    return d


def test_signed_config_loads_anywhere_unsigned_not():
    assert C.parse(signed_doc(), env="pilot").signed
    with pytest.raises(C.PracticeConfigError):
        C.parse(signed_doc(signed_by=None, signed_at=None), env="pilot")
    assert not C.parse(signed_doc(signed_by=None, signed_at=None), env="dev").signed


@pytest.mark.parametrize("over", [
    {"round_delay_hours": [0, 48]},                 # 长度不等于 max_rounds
    {"round_delay_hours": [1, 48, 120]},            # 第 1 轮必须立即可做
    {"round_delay_hours": [0, 120, 48]},            # 递减
    {"round_delay_hours": [0, -1, 48]},
    {"round_delay_hours": [0, 48.5, 120]},
    {"max_rounds": 0, "round_delay_hours": []},
    {"lookback_days": True},                        # 布尔不是整数
    {"lookback_days": 0},
    {"queue_cap": 1000},
    {"variants_per_round": 21},
    {"prefer_new_family": 1},
    {"subjects_enabled": ["aya", "echo"]},          # 英语不涉及
    {"subjects_enabled": ["aya", "aya"]},
    {"format": "other/1"},
    {"signed_by": "x", "signed_at": "2026-10-01T00:00:00+08:00"},   # 合成占位不能带签字
    {"signed_at": "yesterday", "synthetic": False, "signed_by": "x"},
])
def test_config_rejects_bad_shapes(over):
    with pytest.raises(C.PracticeConfigError):
        C.parse(dict(C.SYNTHETIC, **over), env="test")


def test_config_rejects_unknown_or_missing_keys(tmp_path):
    with pytest.raises(C.PracticeConfigError):
        C.parse({**C.SYNTHETIC, "extra": 1}, env="test")
    d = dict(C.SYNTHETIC)
    del d["queue_cap"]
    with pytest.raises(C.PracticeConfigError):
        C.parse(d, env="test")
    p = tmp_path / "practice.json"
    p.write_text(json.dumps(signed_doc()), encoding="utf-8")
    assert C.load(str(p), env="pilot").signed
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(C.PracticeConfigError):
        C.load(str(p), env="test")
    with pytest.raises(C.PracticeConfigError):
        C.load(str(tmp_path / "missing.json"), env="test")


# ---------- 错题判定（验收：每种不进的情况各有测试） ----------
def test_wrong_conditions():
    now = T0 + H
    assert Q.wrong_reason(att(T0), CFG, now) is None
    assert Q.wrong_reason(att(T0, verdict="partial"), CFG, now) is None
    assert Q.wrong_reason(att(T0, channel="canvas"), CFG, now) is None
    assert Q.wrong_reason(att(T0, level="B"), CFG, now) is None
    cases = {
        Q.NOT_WRONG: [att(T0, verdict="correct"), att(T0, verdict="needs_review"), att(T0, verdict="not_applicable")],
        Q.NO_GRADE: [att(T0, graded=False)],
        Q.PENDING_REVIEW: [att(T0, review=True)],
        Q.IN_APPEAL: [att(T0, reasons=("student_appeal",))],
        Q.LEVEL: [att(T0, level="C"), att(T0, level=None)],
        Q.CHANNEL: [att(T0, channel="photo"), att(T0, channel="voice"), att(T0, channel=None)],
        Q.UNTAGGED: [att(T0, unit=None)],
        Q.TOO_OLD: [att(now - 31 * D)],
    }
    for reason, attempts in cases.items():
        for a in attempts:
            assert Q.wrong_reason(a, CFG, now) == reason, (reason, a)
            assert build([a], now) == [], reason
    assert Q.wrong_reason(att(now - 30 * D + H), CFG, now) is None    # 回看期边界之内
    # 诊断错题默认也进（#8），开关关闭时不进
    assert Q.wrong_reason(att(T0, diag=True), CFG, now) is None
    off = replace(CFG, include_diagnosis_attempts=False)
    assert Q.wrong_reason(att(T0, diag=True), off, now) == Q.DIAGNOSIS


# ---------- 闭环与轮次 ----------
def test_independent_correct_on_another_item_closes():
    w = att(T0, "I1")
    e = one(build([w], T0 + H))
    assert (e.status, e.round, e.source, e.source_item_id, e.anchor) == ("due", 1, "wrong", "I1", w.attempt_id)
    c = ok(T0 + 2 * H, "I2")
    e = one(build([w, c], T0 + 3 * H))
    assert e.status == "closed" and not e.open and e.next_round_at is None


@pytest.mark.parametrize("closer", [
    lambda t: ok(t, "I1"),                                  # 原题做对
    lambda t: ok(t, "I2", hint=1),                          # 提示后做对
    lambda t: ok(t, "I2", hint=None),                       # 没报提示等级
    lambda t: ok(t, "I2", self_report="guessed"),           # 自报“猜的”
    lambda t: ok(t, "I2", review=True),                     # 待复核
    lambda t: ok(t, "I2", level="C"),                       # C 级
    lambda t: ok(t, "I2", reasons=("student_appeal",)),     # 申诉中
    lambda t: ok(t, "I2", unit=U2),                         # 别的单元
    lambda t: att(t, "I2", "partial"),                      # 不是做对（是另一道错题）
])
def test_these_do_not_close(closer):
    w = att(T0, "I1")
    e = one(build([w, closer(T0 + H)], T0 + 2 * H))
    assert e.status != "closed"


def test_correct_before_wrong_does_not_close():
    e = one(build([ok(T0, "I2"), att(T0 + H, "I1")], T0 + 2 * H))
    assert e.status == "due"


def test_correct_on_any_earlier_wrong_item_of_the_entry_does_not_close():
    a, b = att(T0, "I1"), att(T0 + H, "I2")                  # I2 是再练第 1 轮做错（归入同一条目）
    e = one(build([a, b, ok(T0 + 50 * H, "I2")], T0 + 51 * H))
    assert e.status == "due" and e.round == 2
    e = one(build([a, b, ok(T0 + 50 * H, "I3")], T0 + 51 * H))
    assert e.status == "closed"


def test_rounds_waiting_due_and_exhausted():
    w0 = att(T0, "I1")
    w1 = att(T0 + H, "I2")                                   # 第 1 轮做错 → 第 2 轮在 48 小时后
    e = one(build([w0, w1], T0 + 2 * H))
    assert (e.status, e.round, e.rounds_done, e.next_round_at) == ("waiting", 2, 1, T0 + H + 48 * H)
    e = one(build([w0, w1], T0 + H + 48 * H))
    assert (e.status, e.round, e.next_round_at) == ("due", 2, None)
    w2 = att(T0 + 50 * H, "I3")                              # 第 2 轮做错 → 第 3 轮在 120 小时后
    e = one(build([w0, w1, w2], T0 + 51 * H))
    assert (e.status, e.round, e.next_round_at) == ("waiting", 3, T0 + 50 * H + 120 * H)
    w3 = att(T0 + 171 * H, "I4")                             # 第 3 轮做错：做满 max_rounds
    e = one(build([w0, w1, w2, w3], T0 + 172 * H))
    assert (e.status, e.round, e.rounds_done, e.next_round_at) == ("exhausted", 3, 3, None)
    # 再练做错不另开条目：始终只有一条
    assert len(build([w0, w1, w2, w3], T0 + 172 * H)) == 1
    # 做满后另一道题独立做对，仍然闭环
    assert one(build([w0, w1, w2, w3, ok(T0 + 200 * H, "I5")], T0 + 201 * H)).status == "closed"


def test_new_wrong_after_close_opens_new_entry_with_new_anchor():
    w0, c, w1 = att(T0, "I1"), ok(T0 + H, "I2"), att(T0 + 2 * H, "I3")
    e = one(build([w0, c, w1], T0 + 3 * H))
    assert (e.status, e.round, e.anchor, e.source_item_id) == ("due", 1, w1.attempt_id, "I3")


def test_entries_are_per_unit_sorted_and_capped():
    attempts = [att(T0 + i * H, f"I{i}", unit=Q.Unit(f"MATH.SYN.T1.N{i}", "apply")) for i in range(1, 26)]
    entries = build(attempts, T0 + 30 * H)
    assert len(entries) == CFG.queue_cap == 20
    assert entries[0].unit.node_id == "MATH.SYN.T1.N25" and entries[-1].unit.node_id == "MATH.SYN.T1.N6"
    assert len(build(attempts, T0 + 30 * H, capped=False)) == 25
    # 同节点不同层次是两个单元
    two = build([att(T0, unit=U1), att(T0, unit=Q.Unit(U1.node_id, "understand"))], T0 + H)
    assert len(two) == 2


def test_hint_followup_entries():
    hinted = ok(T0, "I1", hint=3)
    task = Q.HintTask("t-1", U1, T0)
    e = one(build([hinted], T0 + H, hint_tasks=[task]))
    assert (e.source, e.status, e.round, e.anchor, e.source_item_id) == ("hint_followup", "due", 1, "t-1", "I1")
    # 同一道题无提示做对不闭环；另一道题独立做对闭环（与 KGE-04 关任务同口径）
    assert one(build([hinted, ok(T0 + H, "I1")], T0 + 2 * H, hint_tasks=[task])).status == "due"
    assert one(build([hinted, ok(T0 + H, "I2")], T0 + 2 * H, hint_tasks=[task])).status == "closed"
    # 单元里有开着的错题条目时，错题条目优先
    w = att(T0 + H, "I3")
    e = one(build([hinted, w], T0 + 2 * H, hint_tasks=[task]))
    assert e.source == "wrong" and e.status == "due"
    # 错题条目已闭环、hint 任务开着：hint 条目出现
    e = one(build([att(T0 - 2 * H, "I5"), ok(T0 - H, "I6"), hinted], T0 + H, hint_tasks=[task]))
    assert e.source == "hint_followup" and e.status == "due"


def test_retest_due_flag_and_entry_id():
    w = att(T0, "I1")
    assert one(build([w], T0 + H, retest=[U1])).retest_due is True
    assert one(build([w], T0 + H, retest=[U2])).retest_due is False
    a, b = Q.entry_id(STUDENT, "aya", U1), Q.entry_id(str(uuid.uuid4()), "aya", U1)
    assert a == Q.entry_id(STUDENT, "aya", U1) and a != b and a != Q.entry_id(STUDENT, "zhixing", U1)
    entries = build([w], T0 + H)
    assert Q.find(entries, STUDENT, "aya", a) is entries[0]
    assert Q.find(entries, STUDENT, "aya", b) is None
    assert Q.counts(entries)["due"] == 1


# ---------- 选题 ----------
def cand(item: str, family: str | None = None, *, unit: Q.Unit = U1, license_state: str = "original") -> S.Candidate:
    view = {"item_ref": f"aya:{item}", "main_node_id": unit.node_id, "cognitive_level": unit.cognitive_level,
            "license_state": license_state}
    if family:
        view["family_code"] = family
    c = S.from_view("aya", view)
    assert c is not None
    return c


ADM = {f"C{i}": "admitted" for i in range(20)}


def test_filter_keeps_only_same_unit_admitted_unrestricted_not_done():
    cands = [cand("C1", "f1"), cand("C2", "f2", unit=U2), cand("C3", "f3", license_state="restricted"), cand("C4", "f4"),
             cand("C5", "f5"), cand("C6", "f6"), cand("SRC", "f0")]
    adm = {**ADM, "C4": "pending_review", "SRC": "admitted"}            # C6 未在平台登记（不在 admission 里）
    del adm["C6"]
    f = S.filter_candidates(cands, U1, source_item_id="SRC", exclude_item_ids={"C5"}, admission=adm)
    assert [c.item_id for c in f.kept] == ["C1"] and f.empty_reason is None


@pytest.mark.parametrize("cands,adm,excl,reason", [
    ([], ADM, set(), S.NO_CANDIDATE),
    ([cand("C1", unit=U2)], ADM, set(), S.NO_CANDIDATE),
    ([cand("SRC")], {"SRC": "admitted"}, set(), S.ALL_EXCLUDED),
    ([cand("C1", license_state="restricted")], ADM, set(), S.NO_ADMITTED),
    ([cand("C1")], {"C1": "source_unverified"}, set(), S.NO_ADMITTED),
    ([cand("C1")], {}, set(), S.NO_ADMITTED),
    ([cand("C1"), cand("C2")], ADM, {"C1", "C2"}, S.ALL_EXCLUDED),
])
def test_filter_empty_reasons(cands, adm, excl, reason):
    f = S.filter_candidates(cands, U1, source_item_id="SRC", exclude_item_ids=excl, admission=adm)
    assert f.kept == () and f.empty_reason == reason


def test_filter_keeps_only_objective_items():
    """首发只覆盖客观题（审查 S7）：按题面提供者登记的判分方式过滤；没有判分方式的题不出；全不是客观题时给专门的原因。"""
    cands = [cand("C1", "f1"), cand("C2", "f2"), cand("C3", "f3")]
    grading = {"C1": "model_rubric", "C2": "validator", "C3": "deterministic_rubric"}
    objective = lambda i: grading.get(i) in S.OBJECTIVE_GRADING  # noqa: E731
    f = S.filter_candidates(cands, U1, source_item_id="SRC", exclude_item_ids=(), admission=ADM, objective=objective)
    assert [c.item_id for c in f.kept] == ["C2", "C3"]
    f = S.filter_candidates([cand("C1"), cand("C9")], U1, source_item_id="SRC", exclude_item_ids=(), admission=ADM, objective=objective)
    assert f == S.Filtered((), S.NO_OBJECTIVE)
    # 不传 objective（列表页没有题面提供者）：不按判分方式过滤
    assert len(S.filter_candidates(cands, U1, source_item_id="SRC", exclude_item_ids=(), admission=ADM).kept) == 3


def test_tier_prefers_new_family_and_falls_back_to_same_family():
    kept = [cand("C3", "f-old"), cand("C2", "f-new"), cand("C1", None)]
    t = S.tier(kept, {"family:f-old"}, prefer_new_family=True)
    assert [c.item_id for c in t.offered] == ["C1", "C2"] and t.same_family_only is False
    t = S.tier([cand("C3", "f-old")], {"family:f-old"}, prefer_new_family=True)
    assert [c.item_id for c in t.offered] == ["C3"] and t.same_family_only is True
    t = S.tier(kept, {"family:f-old"}, prefer_new_family=False)
    assert [c.item_id for c in t.offered] == ["C1", "C2", "C3"] and t.same_family_only is False
    assert S.tier([], set(), prefer_new_family=True) == S.Tier((), False)
    # 没有族的题按单题自成一族：做过它就是“同族”
    assert S.tier([cand("C1")], {"item:C1"}, prefer_new_family=True).same_family_only is True
    assert len(S.tier([cand(f"C{i}", f"f{i}") for i in range(60)], set(), prefer_new_family=True).offered) == S.FAMILY_LIMIT


def test_needs_search_and_merge():
    sim = [cand("C1", "f1"), cand("C2", "f1"), cand("C3", "f3", unit=U2)]
    assert S.needs_search(sim, U1, set(), 1) is False
    assert S.needs_search(sim, U1, {"family:f1"}, 1) is True
    assert S.needs_search(sim, U1, set(), 2) is True
    merged = S.merge(sim, [cand("C2", "f1"), cand("C4", "f4")])
    assert [c.item_id for c in merged] == ["C1", "C2", "C3", "C4"]
    assert S.from_view("aya", {"item_ref": "zhixing:X1", "main_node_id": "N", "license_state": "original"}) is None
    assert S.from_view("aya", {"item_ref": "aya:bad id", "main_node_id": "N", "license_state": "original"}) is None


def test_check_picks():
    offered = [cand("C1", "f1"), cand("C2", "f2")]
    pick = lambda ref, adm="admitted": {"item_ref": ref, "admission": adm}  # noqa: E731
    assert [c.item_id for c in S.check_picks([pick("aya:C2")], offered, ADM)] == ["C2"]
    assert S.check_picks([], offered, ADM) == []
    assert S.check_picks([pick("aya:C9")], offered, ADM) is None                    # 候选集之外
    assert S.check_picks([pick("aya:C1", "pending_review")], offered, ADM) is None  # 结果自称未准入
    assert S.check_picks([pick("aya:C1")], offered, {"C1": "source_unverified"}) is None   # 平台表未准入
    assert S.check_picks([pick("aya:C1"), pick("aya:C1")], offered, ADM) is None    # 重复
    assert S.check_picks(["aya:C1"], offered, ADM) is None


def test_variation_request_matches_contract(monkeypatch):
    monkeypatch.setenv("STARBUDDY_ALLOW_CANDIDATE_CONTRACT", "1")
    from learning.contract import plugin_contract
    ctx = {"request_id": str(uuid.uuid4()), "contract_version": "1.0.0", "subject": "aya", "student_ref": STUDENT,
           "data_class": "L2", "idempotency_key": str(uuid.uuid4())}
    offered = [cand("C1", "f1"), cand("C2")]
    req = S.variation_request(context=ctx, subject="aya", source_item_id="SRC", unit=U1, offered=offered,
                              exclude_item_ids={"C5", "bad id"}, count=1, error_category="yunsuan_cuowu")
    assert plugin_contract("1.0.0").validate("GenerateVariationRequest", req) == []
    assert req["exclude_item_refs"] == ["aya:SRC", "aya:C5"] and "usage_id" not in req["context"]
    assert [f["item_ref"] for f in req["item_family"]] == ["aya:C1", "aya:C2"]
    # 原题固定放第一位再截断（审查 S9）：做过的题超过上限（且排序都在原题之前）时原题也不会被截掉
    many = {f"A{i:04d}" for i in range(S.EXCLUDE_LIMIT + 50)} | {"SRC"}
    req = S.variation_request(context=ctx, subject="aya", source_item_id="SRC", unit=U1, offered=offered, exclude_item_ids=many,
                              count=1, error_category=None)
    refs = req["exclude_item_refs"]
    assert len(refs) == S.EXCLUDE_LIMIT and refs[0] == "aya:SRC" and refs.count("aya:SRC") == 1
    assert refs[1:] == sorted(refs[1:])
    req = S.variation_request(context=ctx, subject="aya", source_item_id=None, unit=U1, offered=offered, exclude_item_ids=(),
                              count=2, error_category=None)
    assert plugin_contract("1.0.0").validate("GenerateVariationRequest", req) == []
    assert req["skill_node"] == f"aya:{U1.node_id}" and "subject_error_category" not in req and "exclude_item_refs" not in req


def test_error_category_only_known_and_not_sensitive():
    codes = S.taxonomy_codes()
    assert codes["aya"]["xinli_xiguan"] is True                              # 敏感类
    assert S.error_category_for_request("aya", "xinli_xiguan", codes) is None
    known = next(c for c, sens in codes["aya"].items() if not sens)
    assert S.error_category_for_request("aya", known, codes) == known
    assert S.error_category_for_request("aya", "not_a_code", codes) is None
    assert S.error_category_for_request("zhixing", known, codes) is None          # 别的学科的编码不传
    assert S.error_category_for_request("aya", None, codes) is None
    assert S.error_category_for_request("aya", "Bad Code", codes) is None


# ---------- 票据 ----------
def ticket(**over) -> T.Ticket:
    base = dict(student=STUDENT, subject="aya", node_id=U1.node_id, cognitive_level="apply",
                anchor="00000000-0000-4000-8000-000000000001", item_id="C1", round=1, issued_at=T.issued(T0))
    base.update(over)
    return T.Ticket(**base)


def test_ticket_roundtrip_and_expiry():
    tok = T.issue(SECRET, ticket())
    assert T.verify(SECRET, tok, now=T0, ttl_minutes=60) == ticket()
    assert T.verify(SECRET, tok, now=T0 + timedelta(minutes=60), ttl_minutes=60) == ticket()
    with pytest.raises(T.TicketError) as e:
        T.verify(SECRET, tok, now=T0 + timedelta(minutes=61), ttl_minutes=60)
    assert e.value.code == "TICKET_EXPIRED"
    with pytest.raises(T.TicketError) as e:                       # 签发时间在未来（超过容差）
        T.verify(SECRET, tok, now=T0 - timedelta(minutes=10), ttl_minutes=60)
    assert e.value.code == "TICKET_INVALID"
    assert T.expires_at(ticket(), 60) == T0 + timedelta(minutes=60)


def test_ticket_forgery_rejected():
    tok = T.issue(SECRET, ticket())
    head, payload, sig = tok.split(".")
    other = T.issue(SECRET, ticket(student=str(uuid.uuid4())))
    forged = [
        T.issue("another-secret-another-secret-0000001", ticket()),    # 别的密钥
        f"{head}.{other.split('.')[1]}.{sig}",                         # 换载荷（跨学生）
        f"{head}.{payload}.{sig[:-2]}AA",                              # 改签名
        f"pt0.{payload}.{sig}", tok + ".x", "", "pt1..", "x" * 2000, None, 42,
        f"{head}.{T._b64(b'{}')}.{T._sign(SECRET, head + '.' + T._b64(b'{}'))[0:0]}",
    ]
    # 用正确密钥签的、但载荷不合格的票据也拒（字段多、少、类型不对）
    for bad in ({"round": True}, {"round": 0}, {"student": "not-a-uuid"}, {"issued_at": "0"}):
        d = {**ticket().__dict__, **bad}
        body = T._b64(json.dumps(d).encode())
        forged.append(f"pt1.{body}.{T._sign(SECRET, 'pt1.' + body)}")
    body = T._b64(json.dumps({**ticket().__dict__, "extra": 1}).encode())
    forged.append(f"pt1.{body}.{T._sign(SECRET, 'pt1.' + body)}")
    for f in forged:
        with pytest.raises(T.TicketError) as e:
            T.verify(SECRET, f, now=T0, ttl_minutes=60)
        assert e.value.code == "TICKET_INVALID", f
    with pytest.raises(ValueError):
        T.issue("short", ticket())
