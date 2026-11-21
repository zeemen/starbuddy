"""SIE-03 纯函数（不连库）：配置与禁用推断阻断、流转表与 0032 一致、编码集合与契约 / 迁移一致、“我其实会”的结论、
验证任务接口桩、预测区间（证据少则宽）、对账（区间内外、偏差节点、四方向）、对账句子检查、触发规则 v1、导出与删除的形状映射、幂等缓存。"""
from __future__ import annotations

import copy
import dataclasses
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from insight.claims import ClaimRow
from insight.dispute import NEXT_STEP, STEPS, StubVerificationScheduler, fact_ref, i_know_outcome, step_allowed, verify_by
from insight.guardian_requests import EXPORT_FORMAT, ExportTicket, erase_result, export_result, ticket_from_erasure, ticket_from_export
from insight.policy import CONFIG_DIR, PolicyError
from insight.predict import EXAM_TYPES, PredictInputError, SentenceClaim, check_sentences, predict_interval, reconcile_plan
from insight.service.contract import contract
from insight.service.errors import ServiceError
from insight.service.tools import IdempotencyCache
from insight.shared import ROOT, forbidden_matcher
from insight.sie03_policy import (ACTIONS, ADJUSTMENTS, DIRECTIONS, ERASE_SCOPES, NEXT_STEPS, check_copy, parse_sie03,
                                  synthetic_sie03)
from insight.triggers import LoggedAction, Suggestion, Transition, TriggerFacts, check_suggestions, evaluate
from support import NODE_A, NODE_B, NODE_C, STUDENT, T0, mrow

UTC = timezone.utc
P3 = synthetic_sie03()
C = contract()
MIG = ROOT / "db" / "migrations"


def doc() -> dict:
    return json.loads((CONFIG_DIR / "sie03.synthetic.json").read_text(encoding="utf-8"))


# ---------- 配置 ----------
def test_synthetic_config_only_in_test_and_unsigned():
    assert P3.synthetic and not P3.signed
    with pytest.raises(PolicyError):
        parse_sie03(doc(), env="dev")
    d = doc()
    d.update(synthetic=False, signed_by="test-signer", signed_at="2026-09-30")
    assert parse_sie03(d, env="dev").signed


@pytest.mark.parametrize("path, value", [
    (("dispute", "verification_items"), 6), (("dispute", "verify_within_hours"), 48), (("exam", "interval", "level"), 0.9),
    (("exam", "card_audience"), ["agent"]), (("triggers", "params_version"), "no-version"),
    (("erase_scopes",), ["claim_ledger", "everything"]), (("exam", "interval", "gate_p", "retain"), 0.2),
    (("triggers", "reduce_load_metric_keys"), ["mood"]),
])
def test_config_rejects_bad_values(path, value):
    d = doc()
    cur = d
    for k in path[:-1]:
        cur = cur[k]
    cur[path[-1]] = value
    with pytest.raises(PolicyError):
        parse_sie03(d, env="test")


@pytest.mark.parametrize("section, code, text", [
    ("next_step", "observe_followups", "【占位】你总是太粗心了"),
    ("direction", "calculation_slip", "【占位】孩子性格偏内向"),
    ("action", "ask_reason", "【占位】你最近情绪低落吗"),
    ("adjustment", "none", "【占位】你是视觉型学习者"),
    ("direction", "time_shortage", "【占位】你们学校的学生一般数学较弱"),
])
def test_forbidden_inference_blocks_config_load(section, code, text):
    """禁用推断 0：占位文案命中 SIE-00 词表 → 配置拒绝加载（阻断）"""
    d = doc()
    d["copy"][section][code] = text
    with pytest.raises(PolicyError):
        parse_sie03(d, env="test")


def test_all_codes_and_placeholders_pass_the_lexicon():
    fm = forbidden_matcher()
    assert check_copy(P3, fm) == []
    for code in (*NEXT_STEPS, *DIRECTIONS, *ADJUSTMENTS, *ACTIONS, *ERASE_SCOPES, "i_know", "one_off_slip", "data_error",
                 "self_statement_recorded", "verification_supported", "verification_not_supported", "data_confirmed",
                 "reminder.review_due", "encourage.progress", "reduce_load.break", "reduce_load.exit_after_errors"):
        assert fm(code) == [] and fm(code.replace("_", " ").replace(".", " ")) == []
    # 反例：词表确实会拦英文编码里的特质标签
    assert check_copy({"direction": {"careless": "【占位】"}}, fm) == ["direction.careless"]


# ---------- 与契约、迁移一致 ----------
def _enum(schema: str, *path: str) -> set[str]:
    node = C.doc["components"]["schemas"][schema]
    for p in path:
        node = node[p]
    return set(node["enum"])


def test_code_sets_match_contract():
    result = C.doc["components"]["schemas"]["InsightDisputeResponse"]["allOf"][1]["properties"]["result"]["properties"]
    assert set(result["next_step"]["enum"]) == set(NEXT_STEPS) == set(NEXT_STEP.values())
    rec = C.doc["components"]["schemas"]["InsightReconcileResponse"]["allOf"][1]["properties"]["result"]["properties"]
    assert set(rec["deviation_directions"]["items"]["enum"]) == set(DIRECTIONS)
    assert set(rec["adjustments"]["items"]["enum"]) == set(ADJUSTMENTS)
    er = C.doc["components"]["schemas"]["InsightEraseResponse"]["allOf"][1]["properties"]["result"]["properties"]
    assert set(er["scopes"]["items"]["enum"]) == set(ERASE_SCOPES)
    assert set(C.doc["components"]["schemas"]["InsightDisputeRequest"]["properties"]["kind"]["enum"]) == {"i_know", "one_off_slip", "data_error"}
    assert set(EXAM_TYPES) == set(C.doc["components"]["schemas"]["ExamSpec"]["properties"]["exam_type"]["enum"])


def test_exam_type_mapping_covers_migration_0022():
    """契约的 exam_type（mock 不分一模二模）→ 库里 0022 exam_record.exam_type 的映射（predict.EXAM_TYPES）：值合起来恰好是库里的取值、
    每个库值只归一个契约值。编排层调 insight.predict 时把 mock1 / mock2 都按 mock 传（specs/web-bff-enum-drift lessons 更正）；
    库里加了考试类型而映射没跟上，预测就会漏掉这类考试。月度计划直接按库值读成绩（monthly.EXAM_TYPES），也须与库相同。"""
    from insight.monthly import EXAM_TYPES as MONTHLY_EXAM_TYPES
    sql = (MIG / "0022_insight__onboarding.up.sql").read_text(encoding="utf-8")
    m = re.search(r"exam_type\s+text NOT NULL CHECK \(exam_type IN \(([^)]*)\)\)", sql)
    assert m, "0022 里找不到 exam_type 的 CHECK（迁移改写了，先更新这里的模式）"
    db = set(re.findall(r"'([^']*)'", m.group(1)))
    mapped = [v for vs in EXAM_TYPES.values() for v in vs]
    assert set(mapped) == db, f"映射的库值 {sorted(mapped)} 与 0022 的 {sorted(db)} 不同"
    assert len(mapped) == len(set(mapped)), "有库值同时归到两个契约值"
    assert set(MONTHLY_EXAM_TYPES) == db


def test_dispute_steps_match_migration_0032():
    sql = (MIG / "0032_insight__dispute.up.sql").read_text(encoding="utf-8")
    body = sql.split("CREATE FUNCTION insight.dispute_step_allowed", 1)[1].split(") t(k, f, s, c)", 1)[0]
    rows = set()
    for k, f, s, c in re.findall(r"\('([a-z_]+)',\s*(NULL(?:::text)?|'[a-z_]+'),\s*'([a-z_]+)',\s*(NULL(?:::text)?|'[a-z_]+')\)", body):
        rows.add((k, None if f.startswith("NULL") else f.strip("'"), s, None if c.startswith("NULL") else c.strip("'")))
    assert rows == set(STEPS) and len(rows) == 16


def test_code_sets_match_migrations():
    s33 = (MIG / "0033_insight__intervention_log.up.sql").read_text(encoding="utf-8")
    assert set(re.findall(r"'([a-z_]+)'", s33.split("action         text NOT NULL CHECK (action IN (", 1)[1].split(")", 1)[0])) == set(ACTIONS)
    s34 = (MIG / "0034_insight__exam_prediction.up.sql").read_text(encoding="utf-8")
    adj = s34.split("ARRAY['claims_to_verify'", 1)[1].split("]", 1)[0]
    assert {"claims_to_verify", *re.findall(r"'([a-z_]+)'", adj)} == set(ADJUSTMENTS)
    dirs = s34.split("ARRAY['item_type_transfer'", 1)[1].split("]", 1)[0]
    assert {"item_type_transfer", *re.findall(r"'([a-z_]+)'", dirs)} == set(DIRECTIONS)
    s35 = (MIG / "0035_platform__erasure.up.sql").read_text(encoding="utf-8")
    scopes = s35.split("erase_scope        text CHECK (erase_scope IN (", 1)[1].split(")", 1)[0]
    assert set(re.findall(r"'([a-z_]+)'", scopes)) == set(ERASE_SCOPES)


# ---------- 异议 ----------
def test_step_table_and_refs():
    assert step_allowed("i_know", "open", "verifying", None) and not step_allowed("one_off_slip", "open", "verifying", None)
    assert not any(k == "one_off_slip" and s in ("fact_corrected", "resolved_supported") for k, _f, s, _c in STEPS)
    assert fact_ref("attempt", "a1") == "attempt:a1"
    with pytest.raises(ValueError):
        fact_ref("rumour", "x")
    assert verify_by(T0, 24) == T0 + timedelta(hours=24)


def test_i_know_outcome():
    disputed = T0
    later = T0 + timedelta(hours=2)
    claim_ev = ("e1000000-0000-4000-8000-000000000001",)
    assert i_know_outcome("execute", claim_ev, disputed, None) == "pending"
    assert i_know_outcome("execute", claim_ev, disputed, mrow(gate="stable", evidence=(1, 2), at=T0)) == "pending"      # 不晚于异议
    assert i_know_outcome("execute", claim_ev, disputed, mrow(gate="stable", evidence=(1,), at=later)) == "pending"     # 没有新证据
    assert i_know_outcome("execute", claim_ev, disputed, mrow(gate="stable", evidence=(1, 9), at=later)) == "supported"
    assert i_know_outcome("execute", claim_ev, disputed, mrow(gate="execute", evidence=(1, 9), at=later)) == "not_supported"
    assert i_know_outcome("execute", claim_ev, disputed, mrow(gate="understand", evidence=(1, 9), at=later)) == "conflict"
    assert i_know_outcome(None, claim_ev, disputed, mrow(gate="stable", evidence=(1, 9), at=later)) == "pending"


@pytest.mark.anyio
async def test_stub_scheduler_is_deterministic_and_marked():
    s = StubVerificationScheduler()
    a = await s.schedule(STUDENT, "aya", NODE_A, "d1000000-0000-4000-8000-000000000001", 2)
    b = await s.schedule(STUDENT, "aya", NODE_A, "d1000000-0000-4000-8000-000000000001", 2)
    assert a == b and len(a) == 2 and len({t.task_id for t in a}) == 2 and s.stub
    item_ref = re.compile(C.docs["plugin-api.v0.9.yaml"]["components"]["schemas"]["ItemRef"]["pattern"])
    assert all(item_ref.fullmatch(t.item_ref) and "stub-verify" in t.item_ref for t in a)
    assert await s.describe("aya", [t.task_id for t in a]) == [t.item_ref for t in a]


# ---------- 预测 ----------
IV = P3.exam.interval


def test_interval_bounds_and_determinism():
    rows = [mrow(1, NODE_A, gate="stable", evidence=(1, 2, 3)), mrow(2, NODE_B, gate="execute", evidence=(4, 5))]
    p1 = predict_interval(100, [NODE_A, NODE_B], rows, IV, P3.exam.scales)
    p2 = predict_interval(100, [NODE_A, NODE_B], list(reversed(rows)), IV, P3.exam.scales)
    assert p1 == p2 and 0 <= p1.low <= p1.high <= 100 and p1.node_count == 2 and p1.evidence_count == 5
    assert p1.high < 100 and p1.low > 0  # 中等掌握：区间两端都不贴边（上界不是满分）
    assert p1.scale == "g7_g8_100" and predict_interval(120, [NODE_A], rows, IV, P3.exam.scales).scale == "g9_120"
    assert predict_interval(150, [NODE_A], rows, IV, P3.exam.scales).scale == "custom"
    assert round(p1.low, 1) == p1.low and round(p1.high, 1) == p1.high
    b = p1.basis(100, "sie03-policy@syn-0.1.0", "uniform")
    assert b["node_count"] == 2 and [n["node_id"] for n in b["nodes"]] == [NODE_A, NODE_B]


@pytest.mark.parametrize("full", [100, 120, 150])
def test_interval_narrows_with_more_evidence(full):
    """证据少则宽：同一掌握水平，证据越多区间越窄（先验同为 0.5 时严格变窄）；没有掌握行的节点最宽"""
    widths = []
    for n in (0, 1, 3, 8):
        # p 取先验值 0.5：收缩后的 p̂ 不变，宽度只随证据数变
        rows = [] if n == 0 else [dataclasses.replace(mrow(1, NODE_A, gate="execute", evidence=tuple(range(1, n + 1))), p_mastery=0.5)]
        p = predict_interval(full, [NODE_A], rows, IV, P3.exam.scales)
        widths.append(p.high - p.low)
    assert widths == sorted(widths, reverse=True) and len(set(widths)) == 4


def test_weights_and_type_factors_shift_the_mean():
    import dataclasses
    rows = [dataclasses.replace(mrow(1, NODE_A, gate="retain", evidence=(1, 2, 3, 4, 5)), p_mastery=None),  # 按门槛映射 p
            dataclasses.replace(mrow(2, NODE_B, gate="understand", evidence=(6, 7, 8, 9, 10)), p_mastery=None)]
    even = predict_interval(100, [NODE_A, NODE_B], rows, IV, P3.exam.scales)
    heavy_a = predict_interval(100, [NODE_A, NODE_B], rows, IV, P3.exam.scales, weights={NODE_A: 3, NODE_B: 1})
    hard_a = predict_interval(100, [NODE_A, NODE_B], rows, IV, P3.exam.scales, type_factors={NODE_A: 0.5})
    assert heavy_a.mean > even.mean > hard_a.mean


@pytest.mark.parametrize("kw", [dict(full_score=0), dict(full_score=301), dict(scope=[]), dict(scope=[NODE_A, NODE_A]),
                                dict(scope=["not a node"]), dict(weights={NODE_A: -1}), dict(type_factors={NODE_A: 3})])
def test_interval_input_errors(kw):
    args = dict(full_score=100, scope=[NODE_A], rows=[], params=IV, scales=P3.exam.scales)
    args.update(kw)
    with pytest.raises(PredictInputError):
        predict_interval(**args)


# ---------- 对账 ----------
BASIS = {"nodes": [{"node_id": NODE_A, "p": 0.7, "t": 1, "sd": 0.05}, {"node_id": NODE_B, "p": 0.5, "t": 1, "sd": 0.1}]}


def test_reconcile_directions_only_four_values():
    inside = reconcile_plan(80, 70, 90, BASIS, 1.28)
    assert inside.in_interval and inside.directions == () and inside.position == "inside"
    below = reconcile_plan(50, 70, 90, BASIS, 1.28)
    assert not below.in_interval and below.directions == ("diagnosis_optimistic",) and below.blocked is None
    above = reconcile_plan(95, 70, 90, BASIS, 1.28)
    assert above.blocked == "no_direction_for_above_interval" and above.directions == ()
    given = reconcile_plan(95, 70, 90, BASIS, 1.28, directions=["time_shortage", "item_type_transfer", "time_shortage"])
    assert given.directions == ("item_type_transfer", "time_shortage") and given.blocked is None
    assert reconcile_plan(80, 70, 90, BASIS, 1.28, directions=["time_shortage"]).directions == ()  # 区间内一律为空
    for bad in (["careless"], ["low_ability"]):
        with pytest.raises(PredictInputError):
            reconcile_plan(50, 70, 90, BASIS, 1.28, directions=bad)


def test_reconcile_deviation_nodes():
    plan = reconcile_plan(50, 70, 90, BASIS, 1.28, node_results={NODE_A: 0.3, NODE_B: 0.5})
    assert plan.deviation_nodes == (NODE_A,)
    with pytest.raises(PredictInputError):
        reconcile_plan(50, 70, 90, BASIS, 1.28, node_results={NODE_C: 0.3})
    with pytest.raises(PredictInputError):
        reconcile_plan(50, 70, 90, BASIS, 1.28, node_results={NODE_A: 1.5})


def test_sentence_check_mirrors_0034_guard():
    now = T0
    cid = "c1a00000-0000-4000-8000-000000000001"
    ok = SentenceClaim(cid, STUDENT, "syn.exam.reconcile", True, "hypothesis", ("agent", "student", "guardian"), now + timedelta(days=1),
                       ("time_shortage",))
    s = [{"claim_id": cid, "template_key": "syn.exam.reconcile"}, {"claim_id": cid, "template_key": "syn.exam.reconcile",
                                                                   "direction": "time_shortage"}]
    assert check_sentences(s, {cid: ok}, STUDENT, ["student", "guardian"], now) == []
    import dataclasses
    for bad in (dataclasses.replace(ok, visibility=("agent", "student")), dataclasses.replace(ok, status="to_verify"),
                dataclasses.replace(ok, template_approved=False), dataclasses.replace(ok, expires_at=now),
                dataclasses.replace(ok, student_id="c2000000-0000-4000-8000-0000000000c2")):
        assert check_sentences(s, {cid: bad}, STUDENT, ["student", "guardian"], now) != []
    assert check_sentences([{"claim_id": cid, "template_key": "syn.exam.reconcile", "direction": "careless"}], {cid: ok}, STUDENT,
                           ["student"], now) == ["0"]
    # 卡片的方向必须能在判断账本（判断 value 记下的方向）里找到（契约审查 M2）
    assert check_sentences([{"claim_id": cid, "template_key": "syn.exam.reconcile", "direction": "calculation_slip"}], {cid: ok},
                           STUDENT, ["student"], now) == ["0"]
    assert check_sentences([{"claim_id": cid, "template_key": "syn.exam.reconcile", "text": "x"}], {cid: ok}, STUDENT,
                           ["student"], now) == ["0"]
    assert check_sentences([], {cid: ok}, STUDENT, ["student"], now) == ["count"]


# ---------- 触发规则 ----------
def claim(i: int, status: str = "established", target_type: str = "node", target_id: str = NODE_A, expires: timedelta = timedelta(days=20),
          student: str = STUDENT) -> ClaimRow:
    return ClaimRow(f"c1a00000-0000-4000-8000-{i:012d}", student, "aya" if target_type != "metric" else None, target_type, target_id,
                    "syn.math.node_mastery" if target_type == "node" else "syn.metric.middle_circle", 1, {}, 0.5,
                    ("e1000000-0000-4000-8000-000000000001",), None, None, "inferred", "algo:x@1", status,
                    ("agent", "student", "guardian"), T0 - timedelta(days=30), T0 + expires, T0 - timedelta(days=1))


TP = P3.triggers


def test_trigger_rules():
    claims = (claim(1, expires=timedelta(days=2)), claim(2), claim(3, "to_verify", expires=timedelta(days=1)),
              claim(4, "hypothesis", "metric", "persistence"), claim(5, "hypothesis", "metric", "independent_solve_rate.aya"),
              claim(6, "hypothesis", expires=timedelta(days=-1)))
    transitions = (Transition(claim(2).id, "established", "evidence_threshold", T0 - timedelta(days=2)),
                   Transition(claim(3).id, "established", "verification_supported", T0 - timedelta(days=3)),  # 现在是待验证：不鼓励
                   Transition(claim(1).id, "established", "evidence_threshold", T0 - timedelta(days=30)))     # 窗口外
    facts = TriggerFacts(claims, transitions, activity=(T0 - timedelta(days=5),),
                         exits_after_errors=(T0 - timedelta(days=1), T0 - timedelta(days=2)))
    got = evaluate(facts, TP, T0)
    assert Suggestion("reminder.review_due", "plan_review_node", (claim(1).id,)) in got
    assert Suggestion("encourage.progress", "encourage_with_evidence", (claim(2).id,)) in got
    for rule in ("reduce_load.break", "reduce_load.exit_after_errors"):
        assert {s.action for s in got if s.rule_key == rule} == {"shorten_task", "insert_familiar_item", "ask_reason"}
        assert all(s.claim_ids == (claim(4).id,) for s in got if s.rule_key == rule)  # 只引用配置的指标
    refs = {c for s in got for c in s.claim_ids}
    assert claim(3).id not in refs and claim(6).id not in refs  # 待验证、已过期的判断一律不引用
    check_suggestions(got, claims, STUDENT, T0)
    with pytest.raises(ValueError):
        check_suggestions([Suggestion("encourage.progress", "encourage_with_evidence", (claim(3).id,))], claims, STUDENT, T0)
    with pytest.raises(ValueError):
        check_suggestions(got, claims, "c2000000-0000-4000-8000-0000000000c2", T0)
    # 去重：同规则、同动作、同判断集合在窗口内已有记录的不再生成
    logged = tuple(LoggedAction(s.rule_key, s.action, frozenset(s.claim_ids), T0 - timedelta(hours=1)) for s in got)
    assert evaluate(TriggerFacts(claims, transitions, facts.activity, facts.exits_after_errors, logged), TP, T0) == []


def test_trigger_rules_quiet_without_signals_or_claims():
    claims = (claim(2), claim(4, "hypothesis", "metric", "persistence"))
    quiet = TriggerFacts(claims, (), activity=(T0 - timedelta(hours=3),), exits_after_errors=(T0 - timedelta(days=1),))
    assert evaluate(quiet, TP, T0) == []
    # 连错退出没有比上一个窗口多：不降负
    flat = TriggerFacts(claims, (), (T0 - timedelta(hours=3),),
                        (T0 - timedelta(days=1), T0 - timedelta(days=2), T0 - timedelta(days=8), T0 - timedelta(days=9)))
    assert evaluate(flat, TP, T0) == []
    # 没有可引用的指标判断：不降负（判断先于展示）
    no_metric = TriggerFacts((claim(2),), (), (T0 - timedelta(days=10),), (T0 - timedelta(days=1), T0 - timedelta(days=2)))
    assert evaluate(no_metric, TP, T0) == []
    with pytest.raises(ValueError):
        evaluate(quiet, TP, datetime(2026, 10, 1))


# ---------- 导出与删除的形状映射 ----------
def test_export_and_erase_mapping_is_contract_shaped():
    job = "00000000-0000-4000-8000-00000000f501"
    pkg = {"format": EXPORT_FORMAT, "export_job_id": job, "status": "ready", "download_ref": "dl_" + "a" * 64,
           "expires_at": "2026-10-02T08:00:00+08:00", "student_id": STUDENT, "topics": {"claims": [{"x": 1}]}}
    t = ticket_from_export(pkg, STUDENT)
    res = export_result(t)
    assert "topics" not in res and res["status"] == "ready"
    env = {"contract_version": "0.9.0", "result": res, "evidence_ids": [], "confidence": 1, "source_version": "algo:sie-export@0.1.0",
           "model_version": "algo:sie-export@0.1.0", "human_review_required": False}
    assert C.validate("InsightExportResponse", env) == []
    assert C.validate("InsightExportResponse", {**env, "result": export_result(ExportTicket(job, "queued"))}) == []
    with pytest.raises(ValueError):
        ticket_from_export({**pkg, "student_id": "c2000000-0000-4000-8000-0000000000c2"}, STUDENT)
    req = {"id": job, "status": "pending", "confirmed_at": T0, "scopes": None}
    sched = ticket_from_erasure(req, P3.erase_scopes)
    assert sched.status == "scheduled" and sched.scopes == P3.erase_scopes
    done = ticket_from_erasure({**req, "status": "executed", "scopes": ["reports", "claim_ledger"]}, P3.erase_scopes)
    assert done.status == "completed" and done.scopes == ("claim_ledger", "reports")
    for t2 in (sched, done):
        assert C.validate("InsightEraseResponse", {**env, "result": erase_result(t2)}) == []
    for bad in ({**req, "confirmed_at": None}, {**req, "status": "cancelled"}):
        with pytest.raises(ValueError):
            ticket_from_erasure(bad, P3.erase_scopes)


# ---------- 幂等缓存 ----------
def test_idempotency_cache():
    cache = IdempotencyCache(max_entries=2)
    body = {"context": {"request_id": "r1", "idempotency_key": "k" * 16}, "claim_id": "x"}
    key = ("bff", "insight.dispute", STUDENT, "k" * 16)
    d = IdempotencyCache.digest(body)
    assert cache.get(key, d) is None
    cache.put(key, d, {"ok": 1})
    retry = copy.deepcopy(body)
    retry["context"]["request_id"] = "r2"  # 重试换 request_id：同一个请求
    assert cache.get(key, IdempotencyCache.digest(retry)) == {"ok": 1}
    other = copy.deepcopy(body)
    other["claim_id"] = "y"
    with pytest.raises(ServiceError) as e:
        cache.get(key, IdempotencyCache.digest(other))
    assert e.value.status == 409 and e.value.code == "IDEMPOTENCY_CONFLICT"
    cache.put(("a",), d, {}), cache.put(("b",), d, {})
    assert cache.get(key, d) is None  # 超出容量：最早的被淘汰
