"""PLT-22 压测掌握路径（第一步）：合成掌握配置的加载与环境限制、种子不写任务表、复测任务来源核对、
一致性核对（作答 = 计次 = 作答证据 = 单元重算）与重算耗时、状态变化的纯函数，以及报告渲染。全部合成数据，不起栈。"""
from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from loadtest import writepath as W
from loadtest.driver import Runner
from loadtest.report import render_markdown, render_write_path

BASE = json.loads((W.AYA / "content/pack.json").read_text(encoding="utf-8"))


# ---------------- 合成掌握配置：加载与环境限制 ----------------
def test_mastery_env_points_only_aya_at_tool_copy_of_synthetic_config():
    env = W.mastery_env()
    assert env == {"LEARNING_MASTERY_CONFIG_AYA": str(W.MASTERY_CONFIG)}
    assert W.MASTERY_CONFIG == W.ROOT / "tools/loadtest/config/mastery.synthetic.json"
    doc = W.check_mastery_config()
    assert doc["synthetic"] is True and not doc["signed_by"].strip() and not doc["signed_at"].strip() and doc["subject"] == "aya"
    assert "压测专用副本" in doc["$comment"] and "数值待人确认" in doc["$comment"]


def test_tool_copy_has_the_same_values_as_learning_unit_test_config():
    """副本与学习服务单测配置解耦，但数值一个不改（只有 $comment 不同）：单测配置以后改了，这里会提醒人决定是否同步"""
    copy = json.loads(W.MASTERY_CONFIG.read_text(encoding="utf-8"))
    src = json.loads(W.MASTERY_CONFIG_SOURCE.read_text(encoding="utf-8"))
    copy.pop("$comment"), src.pop("$comment")
    assert copy == src


def test_learning_loader_accepts_synthetic_config_only_in_test_env():
    cfg_mod = pytest.importorskip("learning.mastery.config")
    cfg = cfg_mod.load_config(W.MASTERY_CONFIG, env="test")
    assert cfg.subject == "aya" and not cfg.signed
    with pytest.raises(cfg_mod.ConfigError, match="LEARNING_ENV=test"):
        cfg_mod.load_config(W.MASTERY_CONFIG, env="dev")
    with pytest.raises(cfg_mod.ConfigError):
        cfg_mod.load_config_from_env(W.MASTERY_CONFIG, {"LEARNING_ENV": "prod"})


@pytest.mark.parametrize("patch,needle", [
    ({"synthetic": False}, "synthetic=true"),
    ({"signed_by": "someone", "signed_at": "2026-10-01T00:00:00+08:00"}, "已签字"),
    ({"signed_by": "someone"}, "已签字"),
    ({"subject": "zhixing"}, "学科"),
])
def test_tool_side_check_refuses_non_synthetic_or_signed_or_other_subject(tmp_path, patch, needle):
    doc = json.loads(W.MASTERY_CONFIG.read_text(encoding="utf-8")) | patch
    p = tmp_path / "m.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(RuntimeError, match=needle):
        W.check_mastery_config(p)
    with pytest.raises(RuntimeError):
        W.mastery_env(p)


def test_stack_wires_mastery_env_only_with_write_path_and_test_env():
    from loadtest import stack

    src = inspect.getsource(stack.local_stack)
    assert '"LEARNING_ENV": "test"' in src
    i = src.index("writepath.mastery_env()")
    assert src.rindex("if wp:", 0, i) > src.rindex("lenv = env_base", 0, i)   # 只在写路径接上时给
    assert src.rindex("if mastery:", 0, i) > src.rindex("if wp:", 0, i)       # --no-mastery 时不给


def test_no_mastery_flag_reaches_stack():
    from loadtest.__main__ import main  # noqa: F401  — 只核对参数存在
    from loadtest import __main__ as M, baseline

    assert "--no-mastery" in inspect.getsource(M) and "mastery=not args.no_mastery" in inspect.getsource(M)
    assert "mastery=mastery" in inspect.getsource(baseline.run_baseline)


def test_sync_budget_read_from_learning_config():
    assert W.sync_budget_seconds() == (2.0, "learning.config 缺省")
    assert "retest_not_due" not in inspect.getsource(W) and not hasattr(W, "retest_expected_not_due")


# ---------------- 种子：经 grade 写入证据，不写任务表 ----------------
def test_seed_module_has_no_task_table_write():
    src = inspect.getsource(W)
    for known in W.SERVICE_TASK_WRITES:   # 只作为“服务自己的写入语句”出现在分类表里，工具不执行它们
        src = src.replace(known, "")
    assert not hasattr(W, "RETEST_TASK_SQL")
    assert "INSERT INTO platform.retest_task" not in src and "UPDATE platform.retest_task" not in src
    seed_src = inspect.getsource(W.seed_students)
    for banned in ("execute(", "executemany(", "SET LOCAL ROLE", "acquire("):
        assert banned not in seed_src, banned
    assert W.TASKS_SQL.lstrip().upper().startswith("SELECT")


def test_seeds_do_n2_first_then_the_n1_mistake():
    """先 N2（做对）、后 N1（做错）：重算给两个单元各排的 decay 任务里 N2 的到期更早，复测列表第一条到期条目落在 N2，
    不与再练（N1）撞同一单元"""
    pack = W.synthetic_pack(BASE, W.load_fixture())
    nodes = {i: n for i, n, _ in W.ITEMS}
    assert [k for k, _, _ in W.SEEDS] == ["retest", "practice"]
    (_, r_item, r_ans), (_, p_item, p_ans) = W.SEEDS
    assert (r_item, r_ans) == W.RETEST_SEED and (p_item, p_ans) == W.PRACTICE_SEED
    assert nodes[r_item] == W.N2 and [r_ans["text"]] == W.answer_of(pack, r_item)           # 做对 N2
    assert nodes[p_item] == W.N1 and p_ans["text"] not in W.answer_of(pack, p_item)         # 做错 N1


def test_one_answer_leaves_confidence_below_review_floor_in_synthetic_config():
    """decay 任务当即到期的依据（KGE-04 现有口径，不是时间注入）：合成配置下一道独立作答的置信度低于复核下限，
    decay_crossing_seconds 为 0，到期 = 最后一条计入证据的记录时间。
    质量 = Σ 强度 × 判分置信度，强度 = |证据权重| × 层级权重 × 来源权重 × 错因系数（gates.py _Machine.step）。
    种子走键盘通道 → A 级（层级权重 1）；判分置信度取计入下限 grade_confidence_min 到 1 的两端都核对。"""
    bkt = pytest.importorskip("learning.mastery.bkt")
    cfg_mod = pytest.importorskip("learning.mastery.config")
    cfg = cfg_mod.load_config(W.MASTERY_CONFIG, env="test")
    w, src, floor = cfg["evidence_weights"], cfg["source_weights"]["practice"], cfg["review_confidence_floor"]
    level_a = 1.0
    penalty = cfg.penalty(None)   # 错因未标时按 default
    for gc in (cfg["grade_confidence_min"], 1.0):
        one_right = abs(w["independent"]) * level_a * src * 1.0 * gc
        one_wrong = abs(w["independent_wrong"]) * level_a * src * penalty * gc
        for name, mass in (("一道做对", one_right), ("一道做错", one_wrong)):
            conf = bkt.confidence_of(mass, cfg)
            assert conf < floor, f"{name}（判分置信度 {gc}）置信度 {conf:.4f} 不低于复核下限 {floor}：decay 不会当即到期"
            assert bkt.decay_crossing_seconds(conf, cfg) == 0.0
    # 两道独立做对（判分置信度 1）后高于下限：decay 被撤销。余量很小——判分置信度降到计入下限时恰在下限附近，测试不对它下断言
    two = bkt.confidence_of(2 * abs(w["independent"]) * level_a * src, cfg)
    assert two > floor, f"两道做对后置信度 {two:.4f} 仍不高于下限 {floor}"
    margin = bkt.confidence_of(2 * abs(w["independent"]) * level_a * src * cfg["grade_confidence_min"], cfg) - floor
    if abs(margin) < 0.05:
        import warnings

        warnings.warn(f"余量过小：判分置信度取计入下限 {cfg['grade_confidence_min']} 时，两道做对的置信度与复核下限只差 "
                      f"{margin:+.4f}（验证器判分置信度 1 时不受影响）；换配置或判分置信度变化时 decay 撤销与否可能翻转", UserWarning)


class FakeResponse:
    def __init__(self, verdict: str, status: int = 200):
        self.status_code = status
        self._v = verdict

    def json(self):
        return {"summary": {"verdict": self._v}}


class FakeClient:
    def __init__(self, status: int = 200):
        self.posts, self.usage, self.status = [], [], status

    async def post(self, url, json, headers, timeout):
        ctx = json["request"]["context"]
        self.posts.append((url, ctx["student_ref"], json["request"]["attempt"]["item_ref"]))
        self.usage.append((ctx["student_ref"], ctx["idempotency_key"], ctx["usage_id"]))
        return FakeResponse("incorrect" if json["request"]["attempt"]["response"]["text"] == "0" else "correct", self.status)


class FakeMeter:
    """SeedMeter 的替身（BFF 计量函数）：usage_id 按幂等键派生（同库里的做法，值不同），记下预留、结算、释放"""

    def __init__(self):
        self.reserved, self.settled, self.released = {}, [], []

    async def reserve(self, student, key, fingerprint):
        assert len(fingerprint) == 64
        usage = str(uuid.uuid5(uuid.NAMESPACE_URL, f"synthetic-meter:{key}"))
        self.reserved[usage] = (student, key)
        return usage

    async def settle(self, student, usage_id):
        assert self.reserved[usage_id][0] == student
        self.settled.append(usage_id)

    async def release(self, student, usage_id):
        assert self.reserved[usage_id][0] == student
        self.released.append(usage_id)


class ReadOnlyAdmin:
    """只认两种读：目标核对与任务来源核对；任何写都让测试失败"""

    def __init__(self, rows):
        self.rows, self.reads = rows, []

    async def fetchval(self, sql, *a):
        self.reads.append(sql)
        return True

    async def fetch(self, sql, *a):
        assert sql == W.TASKS_SQL
        self.reads.append(sql)
        return self.rows

    def __getattr__(self, name):
        raise AssertionError(f"种子不应调用 admin.{name}（不写任务表）")


def _task(reason="scheduled", status="open", due=False, proven=True):
    return {"reason": reason, "status": status, "due": due, "proven": proven}


DECAYS = [_task("decay", due=True) for _ in range(4)]   # 两名学生 × 两个单元


def test_seed_students_only_posts_grades_and_reads_back_tasks():
    students = ["5c1e0000-0000-4000-8000-0000000000a1", "5c1e0000-0000-4000-8000-0000000000a2"]
    client, admin, meter = FakeClient(), ReadOnlyAdmin(DECAYS), FakeMeter()
    out = asyncio.run(W.seed_students(client, "http://127.0.0.1:1", "c" * 40, admin, students, 7, BASE["curriculum"], meter=meter))
    assert out["grades"] == 4 and out["verdicts"] == {"practice:incorrect": 2, "retest:correct": 2} and out["manual_tasks"] == 0
    # LT-04：每笔种子先经计量预留（学习服务写入前核对计量，裸写 409），请求带的 usage_id 就是预留返回的那一个；成功即结算，不释放
    assert len(client.usage) == 4 and all(meter.reserved[u] == (s, k) for s, k, u in client.usage)
    assert sorted(meter.settled) == sorted(u for _, _, u in client.usage) and meter.released == []
    assert out["retest_tasks"]["by_reason_status"] == {"decay:open": 4} and out["retest_tasks"]["unproven"] == 0
    assert out["retest_tasks"]["due_open"] == 4
    for s in students:  # 同一学生按先 N2、后 N1 的顺序串行
        assert [i for _, st, i in client.posts if st == s] == [f"aya:{i}" for _, i, _ in W.SEEDS]
    assert all(u.endswith("/internal/v1/grade") for u, _, _ in client.posts)
    assert admin.reads.count(W.TASKS_SQL) == 1


@pytest.mark.parametrize("rows,needle", [
    ([_task("decay", due=True)], "任务不符"),                                     # 到期任务少于学生数
    ([_task("decay", due=False), _task("decay", due=False)], "任务不符"),           # 没有到期的
    (DECAYS + [_task(reason="verify_in_platform", proven=False, due=True)], "任务不符"),  # PLT-21 那种手工行
    ([_task("decay", due=True), _task("decay", due=True, proven=False)], "任务不符"),
])
def test_seed_fails_when_tasks_not_from_recompute(rows, needle):
    students = ["5c1e0000-0000-4000-8000-0000000000a1", "5c1e0000-0000-4000-8000-0000000000a2"]
    with pytest.raises(RuntimeError, match=needle):
        asyncio.run(W.seed_students(FakeClient(), "http://127.0.0.1:1", "c" * 40, ReadOnlyAdmin(rows), students, 7,
                                    BASE["curriculum"], meter=FakeMeter()))


@pytest.mark.parametrize("status,released", [(409, True), (400, True), (500, False), (429, False)])
def test_seed_failure_releases_only_definite_failures(status, released):
    """种子 grade 没成功：确定失败（4xx，408 / 429 除外，例如计量核对不通过的 409）释放预留；结果未知（5xx、429）不释放、
    留给过期任务（同 BFF meteredCall 口径）；都不结算，且整轮种子失败"""
    meter = FakeMeter()
    with pytest.raises(RuntimeError, match="grade 失败"):
        asyncio.run(W.seed_students(FakeClient(status), "http://127.0.0.1:1", "c" * 40, ReadOnlyAdmin(DECAYS),
                                    ["5c1e0000-0000-4000-8000-0000000000a1"], 7, BASE["curriculum"], meter=meter))
    assert meter.settled == [] and len(meter.released) == (1 if released else 0) and len(meter.reserved) == 1


def test_seed_meter_uses_bff_metering_functions_only():
    """SeedMeter 只调 BFF 同一组计量函数（预留、结算、释放，事务里先设 app.account_id），不直写计量表"""
    src = inspect.getsource(W.SeedMeter)
    for fn in ("M.reserve(", "M.settle(", "M.release("):
        assert fn in src, fn
    for banned in ("INSERT", "UPDATE", "usage_reservation", "practice_log", "execute("):
        assert banned not in src, banned
    assert W.SEED_RESOURCE == "mistake_analysis"


def test_task_origins_counts_reason_status_due_and_unproven():
    t = W.task_origins([_task(), _task(status="passed"), _task(due=True), _task(due=True, status="cancelled"),
                        _task(reason="verify_in_platform", proven=False, due=True)])
    assert t == {"total": 5, "by_reason_status": {"scheduled:cancelled": 1, "scheduled:open": 2, "scheduled:passed": 1,
                                                   "verify_in_platform:open": 1},
                 "from_recompute": 4, "unproven": 1, "due_open": 2}


def test_tasks_sql_proves_rounds_by_same_transaction_estimate():
    s = W.TASKS_SQL
    assert "e.id = t.scheduled_by_estimate_id" in s and "e.computed_at = t.created_at" in s and "e.next_retest_at = t.due_at" in s
    assert "student_id = ANY($1::uuid[])" in s


# ---------------- 单元重算次数（pg_stat_statements 快照） ----------------
def test_recompute_statement_matches_the_service_query():
    store = pytest.importorskip("learning.mastery.store")
    import re

    src = re.sub(r"\s+", " ", inspect.getsource(store.current_estimate))
    assert W.RECOMPUTE_STATEMENT in src
    # 只有重算读这条语句（服务里别处没有同样的文本）
    root = W.ROOT / "services/learning/learning"
    hits = [p for p in root.rglob("*.py") if W.RECOMPUTE_STATEMENT in re.sub(r"\s+", " ", p.read_text(encoding="utf-8"))]
    assert [p.relative_to(root).as_posix() for p in hits] == ["mastery/store.py"]
    # 重算在 SET LOCAL ROLE starbuddy_learning 下执行：按语句文本计数、不按登录角色过滤
    assert "rolname" not in W.RECOMPUTE_CALLS_SQL and "starts_with(regexp_replace(s.query" in W.RECOMPUTE_CALLS_SQL


def test_recompute_calls_now_and_stage_diffs():
    class Admin:
        async def fetchval(self, sql, *a):
            assert sql == W.RECOMPUTE_CALLS_SQL and a == (W.RECOMPUTE_STATEMENT,)
            return 42

    assert asyncio.run(W.recompute_calls_now(Admin(), True)) == 42
    assert asyncio.run(W.recompute_calls_now(Admin(), False)) is None
    assert W.stage_diffs([3, 10, 10, 25]) == [7, 0, 15]
    assert W.stage_diffs([3, None, 5]) is None and W.stage_diffs([3]) is None


def test_mastery_log_events_counts_only_failure_events():
    lines = ['{"event": "mastery_after_ingest_timeout", "subject": "aya"}', 'WARNING {"event": "mastery_recompute_failed", "subject": "aya"}',
             '{"event": "mastery_recompute_failed"}', '{"event": "practice_queue"}', '{"method": "grade", "status": 200}', "x {"]
    assert W.mastery_log_events(lines) == {"mastery_after_ingest_timeout": 1, "mastery_after_ingest_failed": 0,
                                           "mastery_recompute_failed": 2}


# ---------------- 一致性核对（四项相等 + 任务来源 + 失败） ----------------
ZERO = dict.fromkeys(W.COUNT_KEYS, 0)
SEEDED = {**ZERO, "response_evidence": 30, "evidence": 60, "evidence_without_count": 30}   # 种子：10 人 × 3 道，不计次
NO_EVENTS = dict.fromkeys(W.MASTERY_EVENTS, 0)


def after(n: int, **kw) -> dict:
    out = {"counted": n, "counted_all": n, "response_evidence": 30 + n, "evidence": 60 + 2 * n, "counted_without_evidence": 0,
           "evidence_without_count": 30} | kw
    return out


TRIGGERS_OK = {"triggers": {"mastery_estimate.guard_inference_insert": True, "retest_task.retest_task_guard": True}, "ok": True}


def m(recomputes, **kw) -> dict:
    base = {"recompute_units": recomputes, "tasks": {"total": 12, "by_reason_status": {"decay:open": 12}, "from_recompute": 12,
                                                      "unproven": 0, "due_open": 12},
            "events": dict(NO_EVENTS), "triggers": TRIGGERS_OK, "task_writes": {"service_calls": 30, "non_service_calls": 0}}
    return base | kw


def test_mastery_consistency_passes_when_four_counts_agree():
    c = W.consistency({"ok": 10, "real": 10, "derived": 0}, SEEDED, after(10), mastery=m(10))
    assert c["ok"] is True and c["problems"] == []
    assert (c["answers_ok"], c["counted_delta"], c["response_evidence_delta"], c["recompute_units"]) == (10, 10, 10, 10)
    assert c["real_share"] == 1.0 and c["triggers"]["ok"] is True and c["task_writes"]["non_service_calls"] == 0
    assert "retest_not_due" not in c


@pytest.mark.parametrize("mastery,needle", [
    (m(9), "单元重算次数 9 不相等"),
    (m(11), "不相等"),
    (m(None), "未采集"),
    (m(10, tasks={"total": 11, "from_recompute": 10, "unproven": 1, "due_open": 0, "by_reason_status": {}}), "不能证明由重算写出"),
    (m(10, events={**NO_EVENTS, "mastery_after_ingest_timeout": 1}), "重算失败 / 超时"),
    (m(10, triggers={"triggers": {"mastery_estimate.guard_inference_insert": True, "retest_task.retest_task_guard": False},
                     "ok": False}), "retest_task.retest_task_guard"),
    (m(10, task_writes={"service_calls": 30, "non_service_calls": 2}), "非学习服务重算语句的写入"),
    (m(10, task_writes=None), "直接证据未采集"),
])
def test_mastery_consistency_flags_each_mismatch(mastery, needle):
    c = W.consistency({"ok": 10, "real": 10, "derived": 0}, SEEDED, after(10), mastery=mastery)
    assert c["ok"] is False and any(needle in p for p in c["problems"]), c["problems"]


def test_consistency_without_mastery_is_unchanged():
    c = W.consistency({"ok": 10, "real": 10, "derived": 0}, SEEDED, after(10))
    assert c["ok"] is True and "recompute_units" not in c


# ---------------- 任务来源：触发器与直接证据 ----------------
def test_triggers_ok_requires_both_present_and_enabled():
    rows = [{"relname": "mastery_estimate", "tgname": "guard_inference_insert", "enabled": "O"},
            {"relname": "retest_task", "tgname": "retest_task_guard", "enabled": "O"}]
    assert W.triggers_ok(rows)["ok"] is True
    assert W.triggers_ok(rows[:1])["ok"] is False
    assert W.triggers_ok([rows[0], {**rows[1], "enabled": "D"}]) == {
        "triggers": {"mastery_estimate.guard_inference_insert": True, "retest_task.retest_task_guard": False}, "ok": False}
    assert "pg_trigger" in W.TRIGGERS_SQL and "NOT t.tgisinternal" in W.TRIGGERS_SQL


def test_triggers_are_the_ones_that_set_timestamps_in_migrations():
    mig = W.ROOT / "db/migrations"
    m5 = (mig / "0005_platform__inference.up.sql").read_text(encoding="utf-8")
    m37 = (mig / "0037_platform__mastery_node_keys.up.sql").read_text(encoding="utf-8")
    assert "CREATE TRIGGER guard_inference_insert BEFORE INSERT ON platform.mastery_estimate" in m5 and "NEW.computed_at := now();" in m5
    assert "CREATE TRIGGER retest_task_guard BEFORE INSERT OR UPDATE ON platform.retest_task" in m37 and "NEW.created_at := now();" in m37


def test_service_task_writes_match_recompute_source():
    import re

    rc = pytest.importorskip("learning.mastery.recompute")
    src = re.sub(r"\s+", " ", inspect.getsource(rc))
    for p in W.SERVICE_TASK_WRITES:
        assert p in src, p


def test_task_writes_classifies_service_and_foreign_statements():
    svc = W.SERVICE_TASK_WRITES[0] + " VALUES ($1::uuid, ...)"
    plt21 = "INSERT INTO platform.retest_task (student_id, subject, node_id, cognitive_level, reason, due_at) VALUES ($1::uuid, 'aya', $2, $3, ...)"
    rows = [{"rolname": "starbuddy_learning", "query": svc, "calls": 7},
            {"rolname": "starbuddy_learning", "query": W.SERVICE_TASK_WRITES[2] + " AND x", "calls": 3},
            {"rolname": "starbuddy_learning", "query": plt21, "calls": 10},       # PLT-21 那种手工写入：列不同
            {"rolname": "postgres", "query": svc, "calls": 1}]                    # 非服务角色
    assert W.task_writes(rows) == {"service_calls": 10, "non_service_calls": 11}
    # pg_stat_statements 的规范化：常量换成 $n、编号重排（实跑里撤销任务就是这种形态）
    normalized = "UPDATE platform.retest_task SET status = $3 WHERE id = $1::uuid AND student_id = $2::uuid"
    assert W.task_writes([{"rolname": "starbuddy_learning", "query": normalized, "calls": 4}]) == {"service_calls": 4,
                                                                                                    "non_service_calls": 0}
    assert W.normalize_sql("a = 'x''y'  AND b = $12") == "a = $? AND b = $?"
    assert "loadtest_ext.pg_stat_statements" in W.TASK_WRITES_SQL and "platform\\.retest_task" in W.TASK_WRITES_SQL


# ---------------- 驱动器：取题没拿到真票据就不发作答 ----------------
def _runner(write_path: bool) -> Runner:
    from loadtest.arrivals import Profile, parse_stages

    p = Profile(seed=1, stages=parse_stages("small"), mean_session_s=6.0, think_mean_s=0.2, practice=True, journeys=("practice_answer",))
    return Runner(p, [], {}, "", "", None, None, {}, write_path=write_path)


def _extras(r: Runner, nexts: dict) -> list[str]:
    from types import SimpleNamespace

    sent: list[str] = []

    async def run(step, fn, expect=frozenset(), expect_codes=frozenset(), path=None):
        sent.append(step)
        return None

    asyncio.run(r.student_extras(SimpleNamespace(index=3), SimpleNamespace(student="s"), None, run, None, "sid", nexts))
    return sent


def test_write_path_runner_skips_answer_without_real_ticket():
    from loadtest.driver import UPSTREAM_MISSING

    r = _runner(True)
    assert _extras(r, {"practice": None, "retest": {"status": "unavailable"}}) == []
    assert r.recorder.notes[("practice.answer", UPSTREAM_MISSING)] == 1 and r.recorder.notes[("retest.answer", UPSTREAM_MISSING)] == 1
    # 写路径没接上时照 PLT-20：派生票据照发（预期 422）
    assert _extras(_runner(False), {}) == ["practice.answer", "retest.answer"]
    assert not hasattr(Runner, "derived_expected") and "retest_not_due" not in inspect.getsource(Runner)


# ---------------- 重算耗时、阶段归属与状态变化 ----------------
T0 = datetime(2026, 10, 19, 2, 0, tzinfo=timezone.utc)
BOUNDS = [T0, T0 + timedelta(seconds=60), T0 + timedelta(seconds=120)]


def row(sec: float, state: str, prev: str | None, unit: float | None, after_ms: float | None) -> dict:
    return {"computed_at": T0 + timedelta(seconds=sec), "state": state, "prev_state": prev, "unit_ms": unit, "after_ms": after_ms}


def test_stage_of_time_edges():
    assert W.stage_of_time(T0 - timedelta(seconds=1), BOUNDS) == 0
    assert W.stage_of_time(T0 + timedelta(seconds=59), BOUNDS) == 0
    assert W.stage_of_time(T0 + timedelta(seconds=60), BOUNDS) == 1
    assert W.stage_of_time(T0 + timedelta(seconds=999), BOUNDS) == 1      # 收尾归最后一段
    assert W.stage_of_time(T0, [T0]) == 0


def test_mastery_timing_overall_and_by_stage():
    rows = [row(1, "unseen", None, 4.0, 10.0), row(2, "understand", "unseen", 6.0, 12.0), row(70, "execute", "understand", 8.0, 30.0),
            row(80, "execute", "execute", None, None)]
    t = W.mastery_timing(rows, BOUNDS, ["0.5×", "1×"])
    o = t["overall"]
    assert (o["written"], o["timed"]) == (4, 3)
    assert o["unit"] == {"p50_ms": 6.0, "p95_ms": 8.0, "max_ms": 8.0} and o["after_answer"]["p50_ms"] == 12.0
    s0, s1 = t["by_stage"]
    assert (s0["label"], s0["written"], s0["unit"]["p95_ms"]) == ("0.5×", 2, 6.0)
    assert (s1["label"], s1["written"], s1["timed"], s1["after_answer"]["max_ms"]) == ("1×", 2, 1, 30.0)
    empty = W.mastery_timing([], BOUNDS, ["a", "b"])
    assert empty["overall"]["unit"] == {"p50_ms": None, "p95_ms": None, "max_ms": None} and len(empty["by_stage"]) == 2


def test_state_changes_counts_transitions_and_same_state_writes():
    rows = [row(1, "unseen", None, 1, 1), row(2, "understand", "unseen", 1, 1), row(3, "understand", "understand", 1, 1),
            row(4, "execute", "understand", 1, 1), row(5, "execute", "understand", 1, 1)]
    assert W.state_changes(rows) == {"changed": 4, "unchanged_state": 1,
                                     "by_transition": {"understand→execute": 2, "unseen→understand": 1, "无→unseen": 1}}


def test_mastery_rows_sql_uses_commit_timestamps_of_the_writing_transaction():
    s = W.MASTERY_ROWS_SQL
    assert "pg_xact_commit_timestamp(p.xmin)" in s and "p.superseded_by = e.id" in s and "e.is_current" in s
    assert "e.computed_at >= $2::timestamptz" in s and "student_id = ANY($1::uuid[])" in s
    assert "unit_ms" in W.MASTERY_STATES_SQL and "pg_xact_commit_timestamp" not in W.MASTERY_STATES_SQL


def test_commit_timestamps_enabled_only_on_ephemeral_restart():
    from loadtest import dbstats

    assert "track_commit_timestamp=on" in inspect.getsource(dbstats.enable_stat_statements)


# ---------------- 报告 ----------------
def _write(check: dict, timing: dict | None = None) -> dict:
    return {"enabled": True, "note": None,
            "content": {"map_version": W.MAP_VERSION, "items": len(W.ITEMS), "units": [W.N1, W.N2], "level": W.LEVEL, "check": {}},
            "seeded": {"students": 10, "grades": 20, "verdicts": {"practice:incorrect": 10, "retest:correct": 10},
                       "retest_tasks": {"total": 20, "by_reason_status": {"decay:open": 20}, "from_recompute": 20, "unproven": 0,
                                        "due_open": 20}, "manual_tasks": 0},
            "check": check, "upstream_missing": {"practice": 2, "retest": 1},
            "mastery": {"enabled": True,
                        "config": {"file": "tools/loadtest/config/mastery.synthetic.json", "config_version": "syn-0.1.0",
                                   "synthetic": True, "signed": False, "rung0_gap_seconds": 7200, "sync_budget_seconds": 2.0,
                                   "sync_budget_source": "learning.config 缺省"},
                        "recompute_units": 10, "recompute_by_stage": [4, 6],
                        "timing": timing or W.mastery_timing([row(1, "unseen", None, 4.0, 10.0), row(70, "understand", "unseen", 6.0, 12.0)],
                                                             BOUNDS, ["0.5×", "1×"]),
                        "states": W.state_changes([row(1, "unseen", None, 4.0, 10.0), row(70, "understand", "unseen", 6.0, 12.0)]),
                        "commit_timestamps": True},
            "segments": []}


def test_report_states_tasks_from_recompute_and_mastery_numbers():
    check = W.consistency({"ok": 10, "real": 10, "derived": 0}, SEEDED, after(10), mastery=m(10))
    text = "\n".join(render_write_path(_write(check)))
    for label in ("PLT-22 掌握路径", "**工具不写任务表**，任务只由学习服务落库后的掌握重算生成", "压测专用合成副本、未签字",
                  "数值待人确认", "learning.config 缺省", "= 触发的单元重算次数", "| 触发的单元重算次数（pg_stat_statements） | 10 |",
                  "**通过**", "单元事务 p50 ms", "| 0.5× | 4 | 1 |", "| 1× | 6 | 1 |", "无→unseen×1", "unseen→understand×1",
                  "不会点亮星", "不经计量", "上游未取到（未发作答）", "再练 2、复测 1", "推断 + 直接证据", "| 30 / 0 |",
                  "retest_task.retest_task_guard:启用", "撤销", "没有测到", "decay:open×20", "推断能证明由重算写出 20、不能证明 0"):
        assert label in text, label
    for gone in ("手工种入", "掌握更新：本栈不设掌握配置", "不到期", "只计再练"):
        assert gone not in text, gone
    bad = W.consistency({"ok": 10, "real": 10, "derived": 0}, SEEDED, after(10), mastery=m(9))
    assert "**不通过**" in "\n".join(render_write_path(_write(bad)))


def test_report_budget_marked_as_assumption_when_unread():
    check = W.consistency({"ok": 10, "real": 10, "derived": 0}, SEEDED, after(10), mastery=m(10))
    w = _write(check)
    w["mastery"]["config"] |= {"sync_budget_seconds": None, "sync_budget_source": "假设"}
    assert "2 s（**假设**" in "\n".join(render_write_path(w))


def test_report_control_run_without_mastery():
    check = W.consistency({"ok": 10, "real": 10, "derived": 0}, SEEDED, after(10))
    w = _write(check)
    w["mastery"] = {"enabled": False, "recompute_units": 0, "tasks": {"total": 0}, "task_writes": {"service_calls": 0,
                                                                                                    "non_service_calls": 0}}
    w["seeded"]["retest_tasks"] = {"total": 0, "by_reason_status": {}, "from_recompute": 0, "unproven": 0, "due_open": 0}
    text = "\n".join(render_write_path(w))
    assert "对照（--no-mastery）" in text and "| 0 / 0 |" in text and "### 掌握重算" not in text and "本次 --no-mastery" in text


def test_report_rates_are_percent_with_two_decimals():
    from loadtest.report import _pct

    assert _pct(0.0073) == "0.73%" and _pct(0.0) == "0.00%" and _pct(None) == "—"


def test_report_flags_missing_commit_timestamps():
    check = W.consistency({"ok": 1, "real": 1, "derived": 0}, SEEDED, after(1), mastery=m(1))
    w = _write(check)
    w["mastery"]["commit_timestamps"] = False
    assert "耗时未测" in "\n".join(render_write_path(w))


# ---------------- 样例：由工具生成、含掌握测量 ----------------
SAMPLE = W.ROOT / "tools/loadtest/sample"


def test_sample_peak2x_has_mastery_measurement_and_no_manual_tasks():
    r = json.loads((SAMPLE / "peak2x.json").read_text(encoding="utf-8"))
    md = (SAMPLE / "peak2x.md").read_text(encoding="utf-8")
    assert render_markdown(r) == md
    w = r["write_path"]
    assert w["enabled"] is True and w["seeded"]["manual_tasks"] == 0
    t = w["check"]["tasks"]
    assert t["unproven"] == 0 and t["total"] == t["from_recompute"] >= w["seeded"]["students"]
    k = w["check"]
    assert k["answers_ok"] == k["counted_delta"] == k["response_evidence_delta"] == k["recompute_units"] > 0
    assert not any(k["recompute_failures"].values())
    assert k["ok"] is True and k["problems"] == [] and k["answers_derived"] == 0
    assert k["triggers"]["ok"] is True and k["task_writes"]["non_service_calls"] == 0 and k["task_writes"]["service_calls"] > 0
    assert w["upstream_missing"] == {"practice": 0, "retest": 0}
    mm = w["mastery"]
    assert mm["enabled"] is True and mm["config"]["file"] == "tools/loadtest/config/mastery.synthetic.json"
    assert mm["config"]["synthetic"] is True and mm["config"]["signed"] is False and mm["commit_timestamps"] is True
    assert mm["config"]["sync_budget_source"] == "learning.config 缺省"
    assert len(mm["timing"]["by_stage"]) == len(r["profile"]["stages"]) == len(mm["recompute_by_stage"])
    assert sum(mm["recompute_by_stage"]) == mm["recompute_units"]
    assert all(b["unit"]["p95_ms"] is not None for b in mm["timing"]["by_stage"] if b["timed"])
    assert "手工种入" not in md and "掌握重算（KGE-04 after_ingest" in md
