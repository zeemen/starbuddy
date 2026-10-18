"""PLT-20（specs/loadtest-2x-peak-plt20）：新场景的步骤与请求构造、票据处理；SLO 判定纯函数（边界、无通过线、未签字不失败）；
peak2x 阶段生成；按阶段汇总；报告格式与 digest。全部不起栈、不连库、合成数据。"""
from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest

from loadtest import bodies, peak, phases, slo
from loadtest.__main__ import MAX_SAFE, main
from loadtest.arrivals import PROFILES, Profile, Stage, describe, digest, peak2x_stages, plan, requests
from loadtest.dbstats import diff_statements, merge_peak
from loadtest.metrics import Recorder, Sample, by_endpoint, by_stage
from loadtest.report import BANNER, PEAK_BANNER, render_markdown, to_json
from loadtest.scenarios import ENDPOINTS, EXTRAS, JOURNEYS, KEYED_EXTRA, PRACTICE_STEPS, journey_steps, keyed, parse_journeys

TOOLS = Path(__file__).resolve().parents[1]
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
NEW_STEPS = {s for _, steps in EXTRAS.values() for s in steps}


# ---------------- 场景与请求序列 ----------------
def test_parse_journeys_order_all_and_unknown():
    assert parse_journeys(None) == () and parse_journeys("") == () and parse_journeys(" , ") == ()
    assert parse_journeys("all") == tuple(k for k in EXTRAS if k != "prediction"), "预测不进 all（只测拒绝路径）"
    assert parse_journeys("all,prediction") == tuple(EXTRAS)
    assert parse_journeys("prediction,my_plan") == ("my_plan", "prediction"), "按 EXTRAS 顺序，与命令行顺序无关"
    assert parse_journeys(["weekly_report", "weekly_report"]) == ("weekly_report",)
    with pytest.raises(ValueError, match="unknown journeys"):
        parse_journeys("my_plan,mock_exam")


def test_default_sequence_unchanged_without_journeys():
    """不开新场景时旅程与请求序列和以前逐项相同"""
    for practice in (False, True):
        base = Profile(seed=11, stages=(Stage(8, 30),), practice=practice)
        assert base.journeys == ()
        assert all(base.steps(k) == journey_steps(k, practice) for k in JOURNEYS)
        assert "journeys" not in describe(base) and all("label" not in s for s in describe(base)["stages"])
    p = Profile(seed=11, stages=(Stage(8, 30),))
    assert all(p.steps(k) == JOURNEYS[k] for k in JOURNEYS)


def test_extras_append_after_host_steps_keep_earlier_seq_and_reproduce():
    on = Profile(seed=11, stages=(Stage(8, 30),), practice=True, journeys=tuple(EXTRAS))
    stu, par = on.steps("student"), on.steps("parent")
    base = tuple("today.orch" if s == "today" else s for s in JOURNEYS["student"])
    assert stu[: len(JOURNEYS["student"]) + len(PRACTICE_STEPS)] == base + PRACTICE_STEPS, "前面各步序号不变（plan_events 只换 today 的标签）"
    assert stu.index("today.orch") == JOURNEYS["student"].index("today")
    assert stu[len(JOURNEYS["student"]) + len(PRACTICE_STEPS):] == tuple(
        s for _, (host, steps) in EXTRAS.items() if host == "student" for s in steps)
    assert par[: len(JOURNEYS["parent"])] == JOURNEYS["parent"] and on.steps("meter") == JOURNEYS["meter"]
    assert stu.index("practice.answer") > stu.index("retest.next"), "作答在取题之后"
    reqs = requests(on)
    assert digest(reqs) == digest(requests(Profile(seed=11, stages=(Stage(8, 30),), practice=True, journeys=tuple(EXTRAS))))
    assert digest(reqs) != digest(requests(Profile(seed=11, stages=(Stage(8, 30),), practice=True)))
    assert all(len(a.think_s) == len(on.steps(a.kind)) for a in plan(on))
    # 新场景的步骤都发生在到达里（混合负载按宿主旅程配比）
    assert NEW_STEPS <= {r.step for r in reqs}


def test_new_steps_have_labels_and_keys_only_on_writes():
    assert NEW_STEPS <= set(ENDPOINTS)
    assert not any(UUID_RE.search(v) for v in ENDPOINTS.values()), "报表行名只有路径模板"
    assert {s for s in NEW_STEPS if keyed(s)} == KEYED_EXTRA
    assert not keyed("diagnose.recheck") and not keyed("practice.answer") and not keyed("my_plan")
    reqs = requests(Profile(seed=9, stages=(Stage(8, 30),), practice=True, journeys=tuple(EXTRAS)))
    keys = [r.key for r in reqs if r.key]
    assert len(keys) == len(set(keys)), "幂等键不重复"
    assert all((r.key is not None) == keyed(r.step) for r in reqs)


def test_profile_validation_for_journeys():
    with pytest.raises(ValueError, match="practice"):
        Profile(journeys=("practice_answer",))
    with pytest.raises(ValueError, match="order"):
        Profile(journeys=("prediction", "my_plan"))
    with pytest.raises(ValueError):
        Profile(journeys=("nope",))
    assert Profile(practice=True, journeys=("practice_answer",)).steps("student")[-2:] == ("practice.answer", "retest.answer")


# ---------------- 请求构造与票据 ----------------
@pytest.mark.parametrize("item,answer", [
    ({"format": "single", "options": [{"key": "B", "text": "x"}, {"key": "C", "text": "y"}]}, {"choice": ["B"]}),
    ({"format": "multi", "options": [{"key": "A", "text": "x"}]}, {"choice": ["A"]}),
    ({"format": "fill", "stem": "s"}, {"text": "1"}),
    (None, {"text": "1"}),
])
def test_answer_request_uses_real_ticket_when_ready(item, answer):
    for kind, prefix in (("practice", "sbp1."), ("retest", "sbr1.")):
        nxt = {"status": "ready", "entry_id": "e", "round": 1, "item": item, "ticket": prefix + "abc", "expires_at": "x"}
        body, codes = bodies.answer_request(kind, nxt, 7, 3)
        assert body == {"ticket": prefix + "abc", "answer": answer} and codes == frozenset()
        assert set(body) == {"ticket", "answer"}, "BFF parseAnswer：键集合恰好是 ticket、answer"


@pytest.mark.parametrize("nxt", [None, {"status": "unavailable", "empty_reason": "no_candidate"}, {"status": "ready", "ticket": "sbr1.x"},
                                 {"status": "ready", "ticket": 5}, {"status": "ready", "ticket": "sbp1." + "x" * 2400}])
def test_answer_request_falls_back_to_derived_ticket(nxt):
    body, codes = bodies.answer_request("practice", nxt, 7, 3)
    assert body["ticket"].startswith("sbp1.") and len(body["ticket"]) <= bodies.MAX_SEALED
    assert body["answer"] == {"text": "1"} and codes == bodies.EXPECT_TICKET_INVALID == frozenset({(422, "TICKET_INVALID")})
    assert bodies.fallback_ticket("practice", 7, 3) == body["ticket"], "确定性"
    assert bodies.fallback_ticket("practice", 7, 4) != body["ticket"] and bodies.fallback_ticket("retest", 7, 3).startswith("sbr1.")


def test_plan_task_ids_and_events():
    t1, t2 = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
    today = {"ordered": {"items": [{"task_id": t1}, {"task_id": "not-a-uuid"}, {"task_id": t1}, {}, {"task_id": t2}], "snapshot": "frozen"}}
    assert bodies.plan_task_ids(today) == [t1, t2]
    assert bodies.plan_task_ids({"subjects": []}) == [] and bodies.plan_task_ids(None) == [] and bodies.plan_task_ids({"ordered": None}) == []
    derived = {"plan.done": "33333333-3333-4333-8333-333333333333", "plan.skip": "44444444-4444-4444-8444-444444444444"}
    keys = {"plan.done": "55555555-5555-4555-8555-555555555555", "plan.skip": "66666666-6666-4666-8666-666666666666"}
    at = bodies.rfc3339()
    two = bodies.plan_events(today, derived, keys, at)
    assert [(s, b["kind"], b["payload"]["plan_task_id"], c) for s, b, c in two] == [
        ("plan.done", "plan_done", t1, frozenset()), ("plan.skip", "plan_skip", t2, frozenset())]
    one = bodies.plan_events({"ordered": {"items": [{"task_id": t1}]}}, derived, keys, at)
    assert one[1][1]["payload"]["plan_task_id"] == derived["plan.skip"] and one[1][2] == bodies.EXPECT_PLAN_TASK_INVALID
    none = bodies.plan_events(None, derived, keys, at)
    assert all(c == frozenset({(422, "INVALID")}) for _, _, c in none)
    for _, b, _ in two:
        # BFF parseEvent：键集合恰好四个、payload 只有 plan_task_id、occurred_at 合 RFC3339（秒以下 ≤ 6 位）、幂等键 UUIDv4
        assert set(b) == {"kind", "payload", "occurred_at", "idempotency_key"} and set(b["payload"]) == {"plan_task_id"}
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})", b["occurred_at"])
        assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", b["idempotency_key"])
    with pytest.raises(ValueError):
        bodies.plan_event("session_start", t1, keys["plan.done"], at)


def test_probe_and_abandon_bodies():
    at = bodies.rfc3339()
    body, codes = bodies.probe_body({"status": "active", "step": {"seq": 3}}, "k", at)
    assert body == {"seq": 3, "prediction": "unsure", "asked_at": at, "idempotency_key": "k"} and codes == frozenset()
    for view in ({"status": "paused", "step": {"seq": 3}}, {"status": "active", "step": None}, None, {"status": "active", "step": {"seq": 0}}):
        body, codes = bodies.probe_body(view, "k", at)
        assert body["seq"] == 1 and codes == bodies.EXPECT_STEP_NOT_CURRENT
    assert bodies.abandon_body("k") == {"idempotency_key": "k"}


def test_outcome_of_expected_codes_and_labels():
    from loadtest.driver import outcome_of

    def prob(status, code):
        return httpx.Response(status, json={"type": "/problems/x", "title": "x", "status": status, "code": code})

    assert outcome_of(prob(422, "TICKET_INVALID"), expect_codes=bodies.EXPECT_TICKET_INVALID) == ("expected", "422 TICKET_INVALID")
    assert outcome_of(prob(422, "INVALID_ANSWER"), expect_codes=bodies.EXPECT_TICKET_INVALID) == ("http_4xx", "422 INVALID_ANSWER")
    assert outcome_of(prob(503, "UNAVAILABLE"), expect_codes=bodies.EXPECT_NO_INSIGHT) == ("expected", "503 UNAVAILABLE")
    assert outcome_of(prob(503, "SUBJECT_BUSY"), expect_codes=bodies.EXPECT_NO_INSIGHT) == ("busy", "503 SUBJECT_BUSY")
    assert outcome_of(httpx.Response(200, json={})) == ("ok", "200")
    # 不给 expect_codes 时与以前相同：只有 SUBJECT_BUSY 带码，别的 503 / 4xx 只写状态码
    assert outcome_of(prob(503, "UNAVAILABLE")) == ("http_5xx", "503")
    assert outcome_of(prob(422, "TICKET_INVALID")) == ("http_4xx", "422")
    assert outcome_of(prob(404, "NOT_FOUND"), expect=frozenset({404})) == ("ok", "404")


def test_expected_outcome_is_not_an_error_and_listed():
    r = Recorder()
    r.stage_windows = {0: [0.0, 10.0]}
    r.add(0, "practice.answer", 5, "expected", "422 TICKET_INVALID")
    r.add(0, "practice.answer", 6, "http_4xx", "422 INVALID_ANSWER")
    d = by_endpoint(r, 10.0)["practice.answer"]
    assert (d["errors"], d["expected"], d["count"]) == (1, 1, 2)
    st = by_stage(r)[0]
    assert st["errors"] == 1 and st["expected"] == 1 and st["throttled_429"] == 0 and st["subject_busy_503"] == 0


def test_journey_env_only_when_enabled():
    from loadtest.stack import journey_env

    assert journey_env(()) == ({}, {}), "不开场景时栈与以前逐项相同"
    bff, learning = journey_env(tuple(EXTRAS))
    assert bff == {"STARBUDDY_TRUST_UI": "weekly,profile,reconcile", "STARBUDDY_STARMAP": "starmap_v1",
                   "STARBUDDY_MONTHLY_PLAN": "monthly_plan", "STARBUDDY_TODAY_ORCH": "today_orch"}
    assert learning == {"LEARNING_TODAY_CONFIG": "synthetic", "LEARNING_TODAY_SNAPSHOT": "1"}
    assert journey_env(("my_plan",))[0] == {"STARBUDDY_MONTHLY_PLAN": "monthly_plan"}
    assert journey_env(("diagnose_more", "practice_answer")) == ({}, {})


# ---------------- SLO 判定 ----------------
def _cfg(**over) -> slo.SloConfig:
    doc = {"format": slo.FORMAT, "status": "待技术负责人签字", "signed_by": None, "signed_at": None, "enforce": False,
           "groups": {"read": {"steps": ["today", "star_map"], "limits": {"p95_ms": 100, "error_rate": 0.1, "throttled_429_rate": 0.0}},
                      "none": {"steps": ["my_plan"], "limits": {}},
                      "grade": {"steps": ["grade.aya"], "limits": {"p95_ms": 30000}}}}
    doc.update(over)
    return slo.parse(doc)


def _samples(ms: list[float], step: str = "today", outcome: str = "ok", stage: int = 0) -> list[Sample]:
    return [Sample(stage, step, m, outcome) for m in ms]


def test_shipped_slo_config_is_unsigned_and_covers_every_step():
    cfg = slo.load()
    assert cfg.status == "待技术负责人签字" and not cfg.signed
    assert cfg.strict() is False and cfg.strict(True) is False, "未签字时无论配置或命令行都不严格"
    grouped = [s for g in cfg.groups for s in g.steps]
    assert len(grouped) == len(set(grouped)) and set(grouped) == set(ENDPOINTS), "每个步骤恰好在一个组里"
    grade = next(g for g in cfg.groups if g.name == "grade")
    assert grade.limits["p95_ms"] == 30000
    assert all(g.limits["p95_ms"] == 800 for g in cfg.groups if g.name in ("login", "student_read", "parent_read"))


def test_judge_boundaries_inclusive():
    cfg = _cfg()
    at_line = slo.evaluate(cfg, _samples([float(x) for x in range(1, 101)]))  # p95 = 95
    g = next(x for x in at_line["groups"] if x["group"] == "read")
    assert g["values"]["p95_ms"] == 95.0 and g["verdict"] == "pass"
    edge = slo.evaluate(cfg, _samples([100.0] * 20))
    assert next(x for x in edge["groups"] if x["group"] == "read")["verdict"] == "pass", "等于上限算通过"
    over = slo.evaluate(cfg, _samples([100.0] * 18 + [100.1] * 2))
    g = next(x for x in over["groups"] if x["group"] == "read")
    assert g["verdict"] == "fail" and g["checks"]["p95_ms"]["verdict"] == "fail" and over["failed_groups"] == ["read"]
    assert not over["all_pass"]


def test_rates_count_errors_429_and_busy_separately():
    cfg = _cfg()
    ss = (_samples([1.0] * 9) + _samples([1.0], outcome="throttled") + _samples([1.0], outcome="expected"))
    g = next(x for x in slo.evaluate(cfg, ss)["groups"] if x["group"] == "read")
    assert g["values"]["count"] == 10 and g["values"]["expected"] == 1, "预期结果不进分母，单列计数"
    assert g["values"]["error_rate"] == 0.1 and g["values"]["throttled_429_rate"] == 0.1 and g["values"]["subject_busy_503_rate"] == 0.0
    assert g["checks"]["error_rate"]["verdict"] == "pass", "错误率 0.1 ≤ 0.1"
    assert g["checks"]["throttled_429_rate"]["verdict"] == "fail", "429 单独有上限"
    assert "subject_busy_503_rate" not in g["checks"], "上限为 null 不判"


def test_percentiles_only_from_ok_and_rejected():
    """契约审查 1：快的预期拒绝与慢的错误都不进分位数"""
    ss = _samples([50.0] * 10) + _samples([1.0] * 30, outcome="expected") + _samples([5000.0] * 2, outcome="http_5xx") \
        + _samples([60.0] * 2, outcome="rejected")
    v = slo.measure(ss)
    assert v["count"] == 14 and v["expected"] == 30 and v["p50_ms"] == 50.0 and v["p99_ms"] == 60.0
    assert v["error_rate"] == pytest.approx(2 / 14)
    only_expected = slo.measure(_samples([1.0] * 3, outcome="expected"))
    assert only_expected["count"] == 0 and only_expected["expected"] == 3 and only_expected["p95_ms"] is None
    g = next(x for x in slo.evaluate(_cfg(), _samples([1.0] * 3, outcome="expected"))["groups"] if x["group"] == "read")
    assert g["verdict"] == "no_samples"


def test_no_threshold_no_samples_and_ungrouped():
    r = slo.evaluate(_cfg(), _samples([5.0]) + _samples([9.0], step="parent.children") + _samples([3.0], step="my_plan"))
    by = {g["group"]: g for g in r["groups"]}
    assert by["none"]["verdict"] == "no_threshold" and by["grade"]["verdict"] == "no_samples"
    assert r["ungrouped_steps"] == ["parent.children"] and r["all_pass"], "无样本、无通过线都不算未达"


def test_per_stage_verdicts():
    ss = _samples([10.0] * 10, stage=0) + _samples([500.0] * 10, stage=1)
    g = next(x for x in slo.evaluate(_cfg(), ss)["groups"] if x["group"] == "read")
    assert [(b["stage"], b["verdict"], b["count"]) for b in g["by_stage"]] == [(0, "pass", 10), (1, "fail", 10)]


def test_unsigned_never_fails_exit_signed_strict_does():
    failing = slo.evaluate(_cfg(), _samples([1000.0] * 5))
    assert not failing["all_pass"]
    unsigned = _cfg(enforce=True)
    assert unsigned.strict(True) is False and slo.exit_ok(failing, unsigned.strict(True))
    signed = _cfg(signed_by="技术负责人（合成）", signed_at="2026-10-18")
    assert signed.signed and signed.strict() is False and signed.strict(True) is True
    assert not slo.exit_ok(failing, signed.strict(True))
    assert _cfg(signed_by="x", signed_at="y", enforce=True).strict() is True
    assert slo.exit_ok(slo.evaluate(signed, _samples([1.0] * 5)), True)


@pytest.mark.parametrize("over,why", [
    ({"format": "x"}, "format"), ({"extra": 1}, "unknown keys"), ({"groups": {}}, "non-empty"),
    ({"groups": {"a": {"steps": ["nope"], "limits": {}}}}, "unknown step"),
    ({"groups": {"a": {"steps": ["today"]}, "b": {"steps": ["today"]}}}, "both"),
    ({"groups": {"a": {"steps": ["today"], "limits": {"p95": 1}}}}, "limits keys"),
    ({"groups": {"a": {"steps": ["today"], "limits": {"error_rate": 1.5}}}}, "rates"),
    ({"groups": {"a": {"steps": ["today"], "limits": {"p95_ms": -1}}}}, "non-negative"),
    ({"groups": {"a": {"steps": ["today"], "limits": {"p95_ms": True}}}}, "non-negative"),
    ({"enforce": "yes"}, "enforce"), ({"signed_by": 3}, "signed_by"),
])
def test_slo_config_validation(over, why):
    with pytest.raises(ValueError, match=why):
        _cfg(**over)


def test_slo_strict_requires_signoff(capsys):
    assert main(["baseline", "--stages", "1:1", "--slo-strict"]) == 2
    assert "签字" in capsys.readouterr().err


# ---------------- peak2x 阶段 ----------------
def test_peak2x_stages_multipliers_and_labels():
    st = peak2x_stages(10, 30, 60)
    assert [(s.concurrency, s.duration_s, s.label) for s in st] == [(5, 30.0, "0.5×"), (10, 30.0, "1×"), (20, 30.0, "2×"), (20, 60.0, "2× 保持")]
    assert [s.concurrency for s in peak2x_stages(5, 1, 1)] == [3, 5, 10, 10], "0.5× 向上取整"
    assert [s.concurrency for s in peak2x_stages(1, 1, 1)] == [1, 1, 2, 2]
    for bad in ((0, 1, 1), (2, 0, 1), (2, 1, 0), (True, 1, 1), (2.5, 1, 1)):
        with pytest.raises(ValueError):
            peak2x_stages(*bad)
    assert st[3].name == "2× 保持" and Stage(5, 1).name == "c5"


def test_shipped_peak_config_is_placeholder():
    cfg = peak.load()
    assert cfg.confirmed is False and cfg.journeys == () and cfg.peak_concurrency == 10
    assert max(s.concurrency for s in cfg.stages()) <= MAX_SAFE, "缺省占位在 20 并发以内，不需要 --i-know"
    assert "占位" in peak.note(cfg, None) and "命令行覆盖" in peak.note(cfg, 20)


@pytest.mark.parametrize("over,why", [({"format": "x"}, "format"), ({"peak_concurrency": 0}, "positive integer"),
                                      ({"peak_concurrency": 2.5}, "positive integer"), ({"ramp_seconds": 0}, "ramp_seconds"),
                                      ({"confirmed": "no"}, "confirmed"), ({"journeys": "all"}, "list"),
                                      ({"journeys": ["nope"]}, "unknown journeys"), ({"x": 1}, "unknown keys")])
def test_peak_config_validation(over, why):
    doc = {"format": peak.FORMAT, "confirmed": False, "basis": "合成", "peak_concurrency": 4, "ramp_seconds": 2, "hold_seconds": 3,
           "journeys": []}
    doc.update(over)
    with pytest.raises(ValueError, match=why):
        peak.parse(doc)


def test_plan_command_peak2x_and_guards(capsys):
    assert main(["plan", "--profile", "peak2x", "--journeys", "my_plan"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [s["concurrency"] for s in out["profile"]["stages"]] == [5, 10, 20, 20]
    assert [s["label"] for s in out["profile"]["stages"]] == ["0.5×", "1×", "2×", "2× 保持"]
    assert out["profile"]["journeys"] == ["my_plan"] and "占位" in out["peak2x"]
    assert main(["plan", "--profile", "peak2x", "--stages", "small"]) == 2
    assert main(["plan", "--peak-concurrency", "5"]) == 2
    assert main(["plan", "--journeys", "nope"]) == 2
    # practice_answer 自动打开再练与复测
    assert main(["plan", "--stages", "2:5", "--journeys", "practice_answer"]) == 0
    assert json.loads(capsys.readouterr().out)["profile"]["practice"] is True
    # 超过 20 并发（1× = 20 → 2× = 40）仍须 --i-know；在起栈之前就拒绝
    assert main(["baseline", "--profile", "peak2x", "--peak-concurrency", "20"]) == 2
    assert "--i-know" in capsys.readouterr().err


def test_plan_command_default_digest_unchanged(capsys):
    assert main(["plan"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["digest"] == digest(requests(Profile(stages=PROFILES["small"]))) and "peak2x" not in out
    assert "journeys" not in out["profile"] and "run_id" not in out["profile"]


@pytest.mark.parametrize("extra,want", [
    ([], "3cd4b8e5ba08775a98c04ce039d7be2bc9672967bd262406f55051af1620db8f"),
    (["--with-practice"], "62a8e0fb3f1dfbbfd0a6312d29e474f071a296d4d76f8e1b362b76bc183dbd72"),
])
def test_digest_regression_pins(capsys, extra, want):
    """契约审查 6：PLT-20 之前的请求序列钉值（不开新场景、不给运行 ID 时必须逐字节不变）"""
    assert main(["plan", "--stages", "5:10,10:10", "--seed", "20260930", *extra]) == 0
    assert json.loads(capsys.readouterr().out)["digest"] == want


def test_run_id_changes_keys_only(capsys):
    """审查 S2：运行 ID 只进幂等键派生；到达、步骤不变，键全部不同；不给时与以前相同"""
    p = Profile(seed=5, stages=(Stage(6, 20),), journeys=("plan_events", "diagnose_more"))
    q = Profile(seed=5, stages=(Stage(6, 20),), journeys=("plan_events", "diagnose_more"), run_id="r2026-10-18a")
    a, b = requests(p), requests(q)
    assert [(r.arrival, r.seq, r.step) for r in a] == [(r.arrival, r.seq, r.step) for r in b]
    ka, kb = {r.key for r in a if r.key}, {r.key for r in b if r.key}
    assert ka and not ka & kb and digest(a) != digest(b)
    assert describe(q)["run_id"] == "r2026-10-18a"
    for bad in ("x" * 33, "a b", "a;b"):
        with pytest.raises(ValueError):
            Profile(run_id=bad)
    assert main(["plan", "--stages", "5:10,10:10", "--seed", "20260930", "--run-id", "r1"]) == 0
    assert json.loads(capsys.readouterr().out)["digest"] != "3cd4b8e5ba08775a98c04ce039d7be2bc9672967bd262406f55051af1620db8f"


def test_today_orch_label_only_with_plan_events():
    assert "today" in journey_steps("student", True, ("my_plan",)) and "today.orch" not in journey_steps("student", True, ("my_plan",))
    s = journey_steps("student", False, ("plan_events",))
    assert "today" not in s and s.index("today.orch") == JOURNEYS["student"].index("today")
    assert "today.orch" in ENDPOINTS and "不与基线" in ENDPOINTS["today.orch"]


# ---------------- 场景核对（审查 S1、S3、S4，契约 5） ----------------
def test_resume_check():
    from loadtest.driver import resume_check

    assert resume_check(200, "s1", "s1") == "resumed_same"
    assert resume_check(201, "s1", "s1") == "resumed_new" and resume_check(200, "s2", "s1") == "resumed_new"


def test_model_budget_stops_at_limit():
    from loadtest.driver import ModelBudget

    b = ModelBudget(3)
    assert b.allows()
    b.spend(2)
    assert b.allows() and not b.stopped
    b.spend(1)
    assert not b.allows() and b.stopped and b.summary() == {"limit": 3, "used": 3, "stopped": True}
    free = ModelBudget()
    free.spend(10_000)
    assert free.allows() and not free.stopped


def test_path_summary_alerts():
    from loadtest.metrics import path_summary

    r = Recorder()
    for _ in range(3):
        r.path("practice.answer", "derived", "expected")
    r.path("practice.answer", "derived", "http_4xx")
    r.path("plan.done", "real", "ok")
    r.path("plan.done", "real", "http_4xx")
    r.path("my_plan", "real", "ok")
    s = path_summary(r)
    assert s["practice.answer"] == {"real": 0, "derived": 4, "real_not_2xx": 0, "derived_not_expected": 1, "expected_share": 0.75,
                                    "alert": True}
    assert s["plan.done"]["real_not_2xx"] == 1 and s["plan.done"]["alert"] and s["plan.done"]["expected_share"] is None
    assert not s["my_plan"]["alert"]
    with pytest.raises(ValueError):
        r.path("x", "fake", "ok")


def test_monthly_plan_expected_only_when_log_says_not_configured():
    from loadtest.baseline import MONTHLY_ROUTE, verify_monthly_expected

    def build():
        r = Recorder()
        r.stage_windows = {0: [0.0, 1.0]}
        for _ in range(3):
            r.add(0, "parent.monthly_plan", 2.0, "expected", "503 UNAVAILABLE")
            r.path("parent.monthly_plan", "derived", "expected")
        return r

    line = json.dumps({"route": MONTHLY_ROUTE, "status": 503, "generate": "not_configured", "duration_ms": 1})
    other = [json.dumps({"route": MONTHLY_ROUTE, "status": 503, "generate": "rate_limited"}),
             json.dumps({"route": "GET /students/{student_id}/today", "status": 503, "generate": "not_configured"}), "not json"]
    r = build()
    assert verify_monthly_expected(r, [line] * 3 + other) == 0
    assert by_endpoint(r, 1.0)["parent.monthly_plan"]["expected"] == 3
    r = build()
    assert verify_monthly_expected(r, [line] + other) == 2, "核对不到的改记为错误"
    d = by_endpoint(r, 1.0)["parent.monthly_plan"]
    assert (d["expected"], d["errors"]) == (1, 2)
    assert d["codes"] == {"503 UNAVAILABLE": 1, "503 UNAVAILABLE（未核对到 not_configured）": 2}
    from loadtest.metrics import path_summary
    assert path_summary(r)["parent.monthly_plan"]["derived_not_expected"] == 2 and path_summary(r)["parent.monthly_plan"]["alert"]
    r = build()
    assert verify_monthly_expected(r, []) == 3 and by_endpoint(r, 1.0)["parent.monthly_plan"]["errors"] == 3


BFF = TOOLS.parents[1] / "services/bff"
NODE_PARSE = """
import { parseEvent } from './src/events/query.ts';
import { parseProbe, parseAbandon } from './src/diagnosis/service.ts';
import { parseAnswer } from './src/practice/service.ts';
import { parseRetestAnswer } from './src/retest/service.ts';
const fns = { parseEvent, parseProbe, parseAbandon, parseAnswer, parseRetestAnswer };
let s = ''; for await (const c of process.stdin) s += c;
console.log(JSON.stringify(JSON.parse(s).map(([fn, body]) => fns[fn](body) !== null)));
"""


def test_bodies_pass_bff_parsers():
    """审查 S3：真路径与派生路径的请求体都能过 BFF 自己的 parse*（直接用 BFF 源码，只解析、不连库）——
    派生路径的拒绝只能来自解票据 / 库里核对任务，而不是请求体不合形状"""
    import shutil
    import subprocess

    from loadtest.arrivals import sid

    if shutil.which("node") is None or not (BFF / "node_modules/pg").exists():
        pytest.skip("没有 node 或 BFF 依赖")
    at = bodies.rfc3339()
    derived = {st: sid("plan-task", 20260930, 7, st) for st in ("plan.done", "plan.skip")}
    keys = {st: sid("key", 20260930, 7, st) for st in ("plan.done", "plan.skip")}
    real = {"ordered": {"items": [{"task_id": "11111111-1111-4111-8111-111111111111"}]}}
    cases = [("parseEvent", b) for today in (None, real) for _, b, _ in bodies.plan_events(today, derived, keys, at)]
    for view in ({"status": "active", "step": {"seq": 2}}, None):
        cases.append(("parseProbe", bodies.probe_body(view, sid("key", 1, 2, "diagnose.probe"), at)[0]))
    cases.append(("parseAbandon", bodies.abandon_body(sid("key", 1, 2, "diagnose.abandon"))))
    for kind, fn in (("practice", "parseAnswer"), ("retest", "parseRetestAnswer")):
        cases.append((fn, bodies.answer_request(kind, None, 20260930, 7)[0]))
        ready = {"status": "ready", "item": {"format": "single", "options": [{"key": "A", "text": "x"}]},
                 "ticket": bodies.TICKET_PREFIX[kind] + "sealed"}
        cases.append((fn, bodies.answer_request(kind, ready, 20260930, 7)[0]))
    out = subprocess.run(["node", "--no-warnings", "--input-type=module", "-e", NODE_PARSE], cwd=BFF, input=json.dumps(cases),
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    assert json.loads(out.stdout) == [True] * len(cases)


# ---------------- 按阶段汇总 ----------------
def test_boundaries_and_stage_at():
    d = [30.0, 30.0, 30.0, 60.0]
    assert phases.boundaries(d) == [30.0, 60.0, 90.0, 150.0]
    assert [phases.stage_at(t, d) for t in (0, 29.9, 30, 89.99, 90, 149, 150, 999)] == [0, 0, 1, 2, 3, 3, 3, 3]


def test_split_log_by_offsets():
    data = b'{"a":1}\n{"gate":"admitted"}\n{"b":2}\n{"c":3}\n'
    first, second = len(b'{"a":1}\n'), len(b'{"a":1}\n{"gate":"admitted"}\n{"b')
    segs = phases.split_log(data, [first, second, len(data)])
    assert segs == [['{"gate":"admitted"}'], ['{"b":2}', '{"c":3}']], "启动日志不算；写了一半的行归下一段；最后一段取到末尾"
    assert phases.split_log(data, [0]) == [] and phases.split_log(b"", [0, 0, 0]) == [[], []]


def test_diff_statements_and_merge_peak():
    before = {("bff_app", 1): {"query": "SELECT $1", "calls": 10, "total_ms": 5.0}}
    after = {("bff_app", 1): {"query": "SELECT $1", "calls": 30, "total_ms": 25.0},
             ("learning_app", 2): {"query": "UPDATE x", "calls": 2, "total_ms": 40.0},
             ("learning_app", 3): {"query": "SELECT idle", "calls": 0, "total_ms": 0.0}}
    assert diff_statements(before, after) == [
        {"role": "learning_app", "query": "UPDATE x", "calls": 2, "total_ms": 40.0, "mean_ms": 20.0},
        {"role": "bff_app", "query": "SELECT $1", "calls": 20, "total_ms": 20.0, "mean_ms": 1.0}]
    peaks: dict = {}
    merge_peak(peaks, "bff_app", 5, 2, 0, 1)
    merge_peak(peaks, "bff_app", 3, 4, 1, 0)
    assert peaks == {"bff_app": {"max_connections": 5, "max_active": 4, "max_lock_wait": 1, "max_waiting": 1}}


# ---------------- 报告格式 ----------------
STUDENT = "0a1b2c3d-0000-4000-8000-00000000abcd"   # 合成：报告里不得出现


def _report(peak_note: str | None) -> dict:
    stages = peak2x_stages(4, 2, 3) if peak_note else (Stage(2, 5.0), Stage(4, 5.0))
    prof = Profile(seed=3, stages=stages, practice=True, journeys=tuple(EXTRAS))
    rec = Recorder()
    t = 0.0
    for i, s in enumerate(stages):
        rec.stage_windows[i] = [t, t + s.duration_s]
        t += s.duration_s
        rec.add(i, "today", 10.0 + i, "ok", "200")
        rec.add(i, "parent.prediction", 3.0, "expected", "503 UNAVAILABLE")
        rec.add(i, "grade.aya", 900.0, "ok", "ok")
    rows = by_stage(rec)
    detail = phases.assemble(rows, [s.label for s in stages], [s.concurrency for s in stages],
                             {"0": {"bff_app": {"max_connections": 4, "max_active": 2, "max_lock_wait": 0, "max_waiting": 1}}},
                             [{}, {("bff_app", 1): {"query": "SELECT a | b", "calls": 3, "total_ms": 9.0}}] + [{}] * (len(stages) - 1),
                             [['{"gate_subject":"aya","gate":"queued","gate_queue_ms":"lt100"}']] + [[]] * (len(stages) - 1))
    reqs = requests(prof)
    return {"banner": PEAK_BANNER if peak_note else BANNER, "scale_note": None, "profile": describe(prof), "arrivals": 3,
            "arrivals_by_journey": {"student": 2, "parent": 1}, "seeded": {}, "plan_digest": digest(reqs), "executed_digest": digest(reqs),
            "wall_seconds": t, "queue_wait_p95_ms": 1.0, "stages": rows, "stage_detail": detail, "endpoints": by_endpoint(rec, t),
            "skipped": {}, "peak2x": {"note": peak_note} if peak_note else None,
            "extras_check": {"paths": {"parent.prediction": {"real": 0, "derived": 4, "real_not_2xx": 0, "derived_not_expected": 0,
                                                             "expected_share": 1.0, "alert": False}},
                             "notes": {"diagnose.resume:resumed_new": 1}, "monthly_plan_demoted": 0},
            "model_budget": {"limit": 50, "used": 50, "stopped": True},
            "slo": {**slo.evaluate(slo.load(), rec.samples), "strict": False},
            "provider": {"median_ms": 300.0, "p90_ms": 900.0, "counts": {}}, "faults": {}, "retry_scale": 1.0,
            "subject_gate": {"enabled": True, "custom_config": False, "by_subject": {}},
            "db": {"activity": {"samples": 1, "by_role": {}}, "top_queries": [], "stat_statements": True, "pool_note": "池"},
            "metering": {"skew": "3 hours", "expired_by_job": 0, "reservations": 0, "settled": 0, "released": 0, "expired": 0,
                         "still_reserved": 0, "unbalanced": 0, "reconciliation_rows": 0, "bucket_reserved": 0, "zero_leak": True},
            "model_calls": {"total": 0, "by_subject_task": {}, "by_outcome": {}}}


def test_peak2x_report_banner_stage_table_and_slo():
    md = render_markdown(_report("1 倍峰值并发 = 4（配置占位，待确认；合成）"))
    first = md.splitlines()[0]
    assert first == f"# 2 倍峰值压测报告（{PEAK_BANNER}）"
    assert md.splitlines()[2].startswith(f"> {PEAK_BANNER}") and "配置占位，待确认" in md
    assert "## 2 倍峰值各阶段" in md and "| 2× 保持 | 8 |" in md and "bff_app:4/2/0/1" in md and "lt100×1" in md
    assert "SELECT a \\| b" in md, "语句里的竖线转义"
    assert "## SLO 判定" in md and "待技术负责人签字" in md and "只报告（未达不影响退出码）" in md
    assert "| grade | 4 | 通过 |" in md and "| metering | 0 | 无样本 |" in md
    assert "按阶段：" in md and "预期" in md and "可选场景（PLT-20）" in md
    assert "## 可选场景核对" in md and "| 100.0% |" in md and "resumed_new" in md and "**告警**" in md
    assert "50 / 50" in md and "已到上限并停止" in md and "today.orch" in md
    assert "| today_orch |" in md and "| gated_read |" in md
    assert STUDENT not in md and not UUID_RE.search(md), "报告不含任何 ID"
    js = to_json(_report("x"))
    assert not UUID_RE.search(js) and json.loads(js)["slo"]["signed"] is False


def test_baseline_report_title_unchanged_and_no_stage_detail():
    md = render_markdown(_report(None))
    assert md.splitlines()[0] == f"# 压测基线报告（{BANNER}）"
    assert "## 2 倍峰值各阶段" not in md and "## SLO 判定" in md
    assert "| 2 并发 |" in md


def test_digest_in_report_matches_plan_for_extras():
    r = _report("x")
    prof = Profile(seed=3, stages=peak2x_stages(4, 2, 3), practice=True, journeys=tuple(EXTRAS))
    assert r["plan_digest"] == digest(requests(prof)) == r["executed_digest"]
    assert f"`{r['plan_digest'][:16]}…`" in render_markdown(r)


def test_sample_peak2x_if_present_is_labelled_and_clean():
    """范围 4 的样例（sample/peak2x.md）生成后：首段标“本机合成，不是容量结论”，不含模型标识与任何 ID"""
    f = TOOLS / "sample/peak2x.md"
    if not f.exists():
        pytest.skip("sample/peak2x.md 尚未生成（本机压测另跑，见 README）")
    text = f.read_text(encoding="utf-8")
    assert PEAK_BANNER in "\n".join(text.splitlines()[:3])
    assert not UUID_RE.search(text) and "fake-a" not in text and "model:" not in text
