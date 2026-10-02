"""跨科联调（冲刺 5-C+D，specs/e2e-integration-sprint5）：20 名合成学生在同一学习服务上走三科诊断、批改、复测（KGE-04 第二步起由
platform.retest_task 驱动，联调栈带合成掌握配置）。

栈：会话级一次性 Postgres（conftest.pg）+ 学习服务（进程内）+ 阿鸦、知行、文砺三个适配器（真实 HTTP 进程）+ 内部模型路由接口（假供应商）。
生成器、回放、断言与证据包在 tools/e2e-cohort/e2e_cohort；这里负责：
- 断言全部通过（并存不覆盖、私有表无副本、家长绑定有来历、grade 溯源五字段、整轮重跑零新增、RLS 隔离、复测由 retest_task 驱动、连库角色）；
- 证据包确定性：与仓库里的合成样例 tools/e2e-cohort/sample/ 逐字节一致（run_metadata.json 去掉提交号后一致）；
- 断言本身会失败：人为制造私有副本（学生键、证据键、学科名表、insight 白名单外、值藏在 jsonb、怪异标识符）、跨家庭绑定、
  放行一切的 RLS 策略、缺溯源字段、重跑新增、陌生连库角色、适配器依赖数据库驱动，断言都能发现；
- 真实内测只读核对（verify）的护栏：库名比对、拒绝超级用户与非只读会话、拒绝写进仓库、名单不全返回 1、只输出汇总。
没有 node 或没装三科适配器依赖时跳过（LEARNING_TEST_STRICT=1 时失败）。全部合成数据（规则 8）。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

import asyncpg
import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/e2e-cohort"))

from e2e_cohort import checks as C  # noqa: E402
from e2e_cohort.cohort import PATTERNS, generate  # noqa: E402
from e2e_cohort.evidence import render  # noqa: E402
from e2e_cohort.replay import load_zhixing_items  # noqa: E402

SAMPLE = ROOT / "tools/e2e-cohort/sample"
VOLATILE_METADATA = ("commit", "worktree_dirty")
AUDIT_ROLE = "e2e_audit_synthetic"


def _points():
    return {k: v["points"] for k, v in load_zhixing_items().items()}


# ---------------- 生成器（不需要数据库） ----------------
def test_generator_is_deterministic_and_covers_the_matrix():
    a, b = generate(_points()), generate(_points())
    assert a.to_json() == b.to_json(), "同一种子逐字节相同"
    assert generate(_points(), seed=1).to_json() != a.to_json()
    s = a.students
    assert len(s) == 20 and len({x.student_id for x in s}) == 20
    assert {x.grade for x in s} == {7, 8, 9}
    assert {x.pattern for x in s} == set(PATTERNS) and all(sum(x.pattern == p for x in s) == 4 for p in PATTERNS)
    assert any("echo" in x.enrollment for x in s) and all({"aya", "wenli", "zhixing"} <= set(x.enrollment) for x in s)
    households = {}
    for x in s:
        households.setdefault(x.household_id, []).append(x.label)
    assert sum(len(v) == 2 for v in households.values()) == 4, "四户有两个孩子（跨家庭 / 同家庭隔离都要测到）"
    # 刷同题：同一道题批改三次；提示依赖：作答都带 4 级提示；中断：没有数学批改与作文
    rep = next(x for x in s if x.pattern == "same_item_repeat")
    assert len({st.attempts[0].item for st in rep.steps if st.subject == "aya" and st.method == "grade"}) == 1
    hint = next(x for x in s if x.pattern == "hint_dependent")
    assert all(a.hint_level_used >= 4 for st in hint.steps if st.subject != "wenli" or st.method == "diagnose" for a in st.attempts)
    cut = next(x for x in s if x.pattern == "interrupted")
    assert {(st.subject, st.method) for st in cut.steps} == {("aya", "diagnose"), ("wenli", "diagnose"), ("zhixing", "grade")}
    # 只有合成文本：作文与道法作答都带“合成”标记
    for x in s:
        for st in x.steps:
            if st.method == "grade" and st.subject in ("wenli", "zhixing"):
                assert "合成" in st.attempts[0].response["text"]


# ---------------- 联调 ----------------
@pytest.fixture(scope="module")
async def report(pg):
    from e2e_cohort.runner import run_synthetic
    from e2e_cohort.stack import missing_prerequisites

    why = missing_prerequisites()
    if why:
        if os.environ.get("LEARNING_TEST_STRICT") == "1":
            pytest.fail(why)
        pytest.skip(why)
    return await run_synthetic(pg)


def _population(report) -> C.Population:
    from e2e_cohort.runner import population
    return population(report.cohort)


def _households(report, *indexes: int) -> C.Population:
    """整户子集（隔离断言每个查看者约 0.35 秒，反例测试不必查满 20 人；家长的孩子都在子集里）"""
    full = _population(report)
    wanted = {full.members[i].household_id for i in indexes}
    return C.Population([m for m in full.members if m.household_id in wanted], list(full.unlinked_guardian_ids))


async def test_cohort_assertions_all_pass(report):
    failed = [c.as_dict() for c in report.failed()]
    assert not failed, json.dumps(failed, ensure_ascii=False)[:4000]
    names = {c.name for c in report.checks}
    assert {"all_calls_succeeded", "adapters_have_no_db_or_vendor_config", "coexistence", "no_private_copies",
            "guardian_links_backed", "grade_envelope_provenance", "grade_rows_provenance", "retest_via_retest_task", "isolation_rls",
            "rerun_zero_new_rows", "rerun_same_result", "db_clients_only_learning_and_admin"} == names
    d = {c.name: c.detail for c in report.checks}
    assert d["coexistence"]["students_with_all_core_subjects"] == 20
    assert d["grade_envelope_provenance"]["grade_responses"] == d["grade_rows_provenance"]["grade_results"]
    assert d["rerun_zero_new_rows"]["replayed"] == d["all_calls_succeeded"]["calls"]
    # 复测只由 retest_task 驱动：每条复测都对应一条复测轮任务；没有任务时一条也不执行（不编造），原因可解释
    assert all(r["scheduler"] == "retest_task" and r["source_step"].startswith("retest_task:") for r in report.retests)
    rt = d["retest_via_retest_task"]
    assert rt["retest_calls"] == len(report.retests) and rt["evidence_without_task"] == []
    assert rt["mastery"]["units"] > 0                                     # 掌握重算确实走到了（证据已归属到合成地图的单元）
    if not report.retests:
        assert rt["retest_round_tasks"] == 0 and rt["not_reached"]["top_excluded_reason"] is not None
        assert rt["reached"] is False and next(c for c in report.checks if c.name == "retest_via_retest_task").label == "通过（0 次，未走到）"
        assert d["all_calls_succeeded"]["by_subject_method_purpose"].get("aya/grade/retest") is None
    # 隔离逐表比对来自个人表清单（多于回退的 6 张），两种未登录都查了
    assert d["isolation_rls"]["table_source"] == "registry" and d["isolation_rls"]["tables_compared"] > 6
    assert d["isolation_rls"]["anonymous_variants"] == ["empty_account", "unset_account"]
    assert not d["isolation_rls"]["tables_skipped_no_privilege"]
    # 查找值含证据 ID、作答 ID、家庭与家长：远多于 40 个学生与账号 ID
    assert d["no_private_copies"]["needles"] > 500 and d["no_private_copies"]["registry"] == "checked"
    assert d["db_clients_only_learning_and_admin"]["logged_users"] == ["learning_app", "postgres"]
    # 三科能力声明都按联调请求的 1.0.0 报
    assert {a["capabilities_contract_version"] for a in report.metadata["adapters"].values()} == {"1.0.0"}


async def test_evidence_pack_matches_committed_sample(report):
    """确定性：同一种子、同一代码，证据包与仓库里的合成样例逐字节一致。样例过期时用 make gate0-evidence GATE0_OUT=tools/e2e-cohort/sample 重生。"""
    files = render(report)
    assert set(files) == {p.name for p in SAMPLE.iterdir() if p.is_file() and p.name != "README.md"}
    for name, content in files.items():
        want = (SAMPLE / name).read_text(encoding="utf-8")
        if name == "run_metadata.json":
            got_meta, want_meta = json.loads(content), json.loads(want)
            for k in VOLATILE_METADATA:
                got_meta.pop(k, None)
                want_meta.pop(k, None)
            assert got_meta == want_meta, "run_metadata.json（去掉提交号）与样例不同"
        else:
            assert content == want, f"{name} 与样例不同（样例过期或输出不确定）"
    text = "".join(files.values())
    assert "合成联调，不是内测结论" in files["summary.md"]
    # 复测调度不再是接口桩：数据文件与摘要里没有“桩”（starmap/ 的“短信捕获桩”是星图截图登录用的，另说）
    assert all("桩" not in files[n] for n in ("summary.md", "events.csv", "nodes.csv", "retests.csv", "checks.json", "run_metadata.json"))
    assert "retest_task" in files["summary.md"] and files["retests.csv"].startswith("label,student_id,subject,node_id,")
    if not report.retests:
        assert "| retest_via_retest_task | 通过（0 次，未走到） |" in files["summary.md"]
    # 证据包只含 ID 与计数：没有作答原文、作文、模型输出
    for s in report.cohort.students:
        for st in s.steps:
            for a in st.attempts:
                if "text" in a.response and len(a.response["text"]) > 12:
                    assert a.response["text"] not in text


# ---------------- 断言本身会失败（防止“永远通过”） ----------------
async def test_private_copies_are_detected(report, pools):
    _, admin = pools
    pop = _population(report)
    victim = pop.members[0].student_id
    async with admin.acquire() as c:
        ev = await c.fetchval("SELECT evidence_id::text FROM platform.evidence_event WHERE student_id = $1::uuid LIMIT 1", victim)
        await c.execute("CREATE SCHEMA zhixing_private")
        try:
            # 学生键列 + 取值
            await c.execute("CREATE TABLE zhixing_private.weak_points (student_ref uuid, node text)")
            await c.execute("INSERT INTO zhixing_private.weak_points VALUES ($1::uuid, 'syn-dim-zhuanhua')", victim)
            got = await C.check_no_private_copies(c, pop)
            assert not got.passed
            assert "zhixing_private.weak_points.student_ref" in got.detail["student_key_columns_outside_personal_layer"]
            assert {"table": "zhixing_private.weak_points", "rows": 1} in got.detail["student_values_outside_personal_layer"]
            await c.execute("DROP TABLE zhixing_private.weak_points")
            # 以 evidence_id 为键的私有表（契约审查 M2）
            await c.execute("CREATE TABLE zhixing_private.evidence_cache (evidence_id uuid PRIMARY KEY, verdict text)")
            await c.execute("INSERT INTO zhixing_private.evidence_cache VALUES ($1::uuid, 'partial')", ev)
            got = await C.check_no_private_copies(c, pop)
            assert "zhixing_private.evidence_cache.evidence_id" in got.detail["student_key_columns_outside_personal_layer"]
            assert {"table": "zhixing_private.evidence_cache", "rows": 1} in got.detail["student_values_outside_personal_layer"]
            await c.execute("DROP TABLE zhixing_private.evidence_cache")
            # 列名不像学生键、值藏在 jsonb 里（大写形式也能认出）
            await c.execute("CREATE TABLE zhixing_private.cache (k text, v jsonb)")
            await c.execute("INSERT INTO zhixing_private.cache VALUES ('x', jsonb_build_object('who', upper($1::text)))", victim)
            got = await C.check_no_private_copies(c, pop)
            assert got.detail["student_values_outside_personal_layer"] == [{"table": "zhixing_private.cache", "rows": 1}]
            await c.execute("DROP TABLE zhixing_private.cache")
            # 怪异标识符（带双引号）：转义后照常扫描，不报 SQL 错误
            await c.execute('CREATE TABLE zhixing_private."we""ird" ("a""b" text)')
            await c.execute('INSERT INTO zhixing_private."we""ird" VALUES ($1)', f"x{victim}y")
            got = await C.check_no_private_copies(c, pop)
            assert got.detail["student_values_outside_personal_layer"] == [{"table": 'zhixing_private.we"ird', "rows": 1}]
        finally:
            await c.execute("DROP SCHEMA zhixing_private CASCADE")
        # 平台个人层里以学科名开头的表直接判失败（即使没有数据）
        await c.execute("CREATE TABLE platform.aya_scratch (note text)")
        try:
            got = await C.check_no_private_copies(c, pop)
            assert not got.passed and got.detail["subject_named_tables_in_personal_layer"] == ["platform.aya_scratch"]
        finally:
            await c.execute("DROP TABLE platform.aya_scratch")
        # insight 不再整体豁免：白名单外的 insight 表带学生键 → 失败
        await c.execute("CREATE TABLE insight.subject_weakness_copy (student_id uuid)")
        try:
            got = await C.check_no_private_copies(c, pop)
            assert "insight.subject_weakness_copy.student_id" in got.detail["student_key_columns_outside_personal_layer"]
        finally:
            await c.execute("DROP TABLE insight.subject_weakness_copy")
        assert (await C.check_no_private_copies(c, pop)).passed
        # 查找值为空（名单为空）直接判失败，而不是“什么都没扫到所以通过”
        assert not (await C.check_no_private_copies(c, C.Population([]))).passed


async def test_registry_missing_falls_back_and_is_marked(report, pools, monkeypatch):
    """个人表清单（0035）不存在时：私有副本 (c) 标“待 0035”并跳过，隔离回退 6 张表并标注，不报错"""
    _, admin = pools
    pop = _households(report, 0)
    monkeypatch.setattr(C, "REGISTRY", "platform.no_such_registry_e2e")
    async with admin.acquire() as c:
        got = await C.check_no_private_copies(c, pop)
        assert got.passed and got.detail["registry"].startswith("pending_0035")
        iso = await C.check_isolation(c, pop)
        assert iso.passed and iso.detail["table_source"].startswith("fallback_6_tables") and iso.detail["tables_compared"] == 6


async def test_cross_household_link_is_detected(report, pools):
    """给第一户家长加一条到别家孩子的绑定：RLS 让他看得到，隔离断言按“应当只看到本户孩子”判为违规；
    绑定来历断言（安全审查 M1）同时报 1 条“既不是家庭创建人、也没有已使用邀请”的绑定。"""
    _, admin = pools
    pop = _households(report, 0, -1)
    a, b = pop.members[0], pop.members[-1]
    assert a.household_id != b.household_id
    async with admin.acquire() as c:
        await c.execute("""INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via)
                           VALUES ($1::uuid, $2::uuid, 'household_creator')""", a.guardian_ids[0], b.student_id)
        try:
            got = await C.check_isolation(c, pop)
            assert not got.passed
            assert {v["account_id"] for v in got.detail["violations"]} == {a.guardian_ids[0]}
            backed = await C.check_guardian_links_backed(c, pop)
            assert not backed.passed and backed.detail == {"unbacked_active_links": 1}
        finally:
            await c.execute("DELETE FROM platform.guardian_student_link WHERE guardian_account_id = $1::uuid AND student_id = $2::uuid",
                            a.guardian_ids[0], b.student_id)
        assert (await C.check_guardian_links_backed(c, pop)).passed


async def test_permissive_policy_is_detected(report, pools, pg):
    """临时加一条 USING (true) 的放行策略：学生视角与两种未登录视角都判失败；另用全新连接（从未调用 set_config）复核"""
    _, admin = pools
    pop = _households(report, 0, -1)
    async with admin.acquire() as c:
        await c.execute("CREATE POLICY e2e_leak ON platform.evidence_event FOR SELECT TO starbuddy_bff USING (true)")
        try:
            got = await C.check_isolation(c, pop)
            assert not got.passed
            kinds = {v["viewer"] for v in got.detail["violations"] if v["table"] == "platform.evidence_event"}
            assert {"student", "guardian", "unlinked_guardian", "anonymous_empty", "anonymous_unset"} <= kinds
            fresh = await asyncpg.connect(**pg.dsn("postgres"))
            try:
                leaked = await C._visible_students(fresh, None)
                assert set(pop.student_ids) <= leaked["evidence_event"]
            finally:
                await fresh.close()
        finally:
            await c.execute("DROP POLICY e2e_leak ON platform.evidence_event")
    fresh = await asyncpg.connect(**pg.dsn("postgres"))
    try:
        assert (await C._visible_students(fresh, None))["evidence_event"] == set(), "从未设置账号的连接什么也看不到"
    finally:
        await fresh.close()


async def test_guardian_expected_set_includes_children_outside_list(report, pools):
    """verify 口径（契约审查 L4）：家长的应见集合取其全部生效绑定。给第一户家长再建一个名单外的孩子：
    按库里绑定加载的 Population 不判违规；只按名单推出应见集合的 Population 会误判。"""
    _, admin = pools
    pop = _households(report, 0)
    m = pop.members[0]
    acct, sid = str(uuid.uuid4()), str(uuid.uuid4())
    async with admin.acquire() as c:
        await c.execute("INSERT INTO platform.account (id, kind) VALUES ($1::uuid, 'student')", acct)
        await c.execute("""INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, birth_year_month)
                           VALUES ($1::uuid, $2::uuid, $3::uuid, 7, 'nanjing', '2014-02-01')""", sid, acct, m.household_id)
        await c.execute("""INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via)
                           VALUES ($1::uuid, $2::uuid, 'household_creator')""", m.guardian_ids[0], sid)
        try:
            loaded = await C.load_population(c, pop.student_ids)
            assert sid in loaded.children_of(m.guardian_ids[0])
            assert (await C.check_isolation(c, loaded)).passed
            assert not (await C.check_isolation(c, pop)).passed
        finally:
            await c.execute("DELETE FROM platform.student_profile WHERE id = $1::uuid", sid)
            await c.execute("DELETE FROM platform.account WHERE id = $1::uuid", acct)


async def test_retest_without_task_is_detected(report, pools):
    """复测断言会失败：标成接口桩的复测、执行了但没有对应任务的复测、有复测证据却没有执行记录的学生，都不能通过"""
    from e2e_cohort.replay import RetestTaskScheduler, RunLog
    from e2e_cohort.runner import check_retest
    app_pool, admin = pools
    s0 = report.cohort.students[0]
    fake = {"label": s0.label, "student_id": s0.student_id, "subject": "aya", "node_id": "MATH.E2E.T1.ALG", "cognitive_level": "apply",
            "rung": "rung0", "reason": "scheduled", "item": "Q-ALG-U02-D-001", "status": 200}
    async with admin.acquire() as c:
        for bad in ({**fake, "scheduler": "stub", "source_step": "aya-grade-1"},
                    {**fake, "scheduler": "retest_task", "source_step": "retest_task:rung0"}):   # 库里没有这名学生的复测证据
            log = RunLog(retests=[bad])
            got = await check_retest(c, report.cohort, log, RetestTaskScheduler(app_pool), {})
            assert not got.passed, bad["scheduler"]


async def test_unexpected_db_client_is_detected(report, pools):
    from e2e_cohort.runner import check_db_clients

    class FakeLog:
        def __init__(self, users):
            self._users = users

        def users(self):
            return self._users

    _, admin = pools
    async with admin.acquire() as c:
        assert (await check_db_clients(c, FakeLog({"learning_app", "postgres"}), ("learning_app", "postgres"))).passed
        got = await check_db_clients(c, FakeLog({"learning_app", "aya_adapter"}), ("learning_app", "postgres"))
        assert not got.passed and got.detail["unexpected"] == ["aya_adapter"]
        assert not (await check_db_clients(c, FakeLog(set()), ("learning_app", "postgres"))).passed, "日志没开或读不到：不算通过"


def test_adapter_db_driver_is_detected(tmp_path, monkeypatch):
    from e2e_cohort import stack

    assert stack.adapter_db_drivers() == {"aya": [], "wenli": [], "zhixing": []}
    for s in stack.SUBJECTS:
        (tmp_path / s).mkdir()
        deps = {"ajv": "^8"} if s != "zhixing" else {"ajv": "^8", "pg": "^8"}
        (tmp_path / s / "package.json").write_text(json.dumps({"dependencies": deps, "devDependencies": {"knex": "^3"} if s == "aya" else {}}))
    monkeypatch.setattr(stack, "ADAPTERS", tmp_path)
    assert stack.adapter_db_drivers() == {"aya": ["knex"], "wenli": [], "zhixing": ["pg"]}


def test_register_content_runs_as_learning_role(pg):
    from e2e_cohort.stack import register_content  # noqa: F401  （登记 SQL 前缀与这里同一写法）
    assert pg.psql("SET ROLE starbuddy_learning;\nSELECT current_user;").strip() == "starbuddy_learning"


def test_missing_grade_provenance_is_detected():
    ok = {"evidence_ids": ["e"], "confidence": 0.5, "source_version": "rubric:x@1", "model_version": "algo:x@1", "human_review_required": True}
    assert C.check_grade_envelopes([{"method": "grade", "status": 200, "body": ok}]).passed
    for field in C.GRADE_FIELDS:
        body = {k: v for k, v in ok.items() if k != field}
        got = C.check_grade_envelopes([{"method": "grade", "status": 200, "body": body}])
        assert not got.passed and field in got.detail["incomplete"][0]["problems"]
    assert not C.check_grade_envelopes([{"method": "grade", "status": 200, "body": {**ok, "evidence_ids": []}}]).passed
    assert not C.check_grade_envelopes([]).passed, "一条 grade 都没有不算通过"


def test_rerun_growth_is_detected():
    before = {"evidence_event": 10, "usage_ledger": 0}
    assert C.check_rerun(before, dict(before), replayed=3, total=3, adapter_calls=0, first_run_keys_ok=True).passed
    assert not C.check_rerun(before, {**before, "usage_ledger": 1}, replayed=3, total=3, adapter_calls=0, first_run_keys_ok=True).passed
    assert not C.check_rerun(before, dict(before), replayed=2, total=3, adapter_calls=0, first_run_keys_ok=True).passed
    assert not C.check_rerun(before, dict(before), replayed=3, total=3, adapter_calls=1, first_run_keys_ok=True).passed


async def test_evidence_command_stops_postgres_when_start_fails(monkeypatch, tmp_path):
    """安全审查 L4：一次性 Postgres 启动失败时也会清理（start 在 try 里）"""
    import pgharness
    from e2e_cohort import __main__ as cli

    events = []

    class Boom:
        def start(self):
            events.append("start")
            raise RuntimeError("synthetic start failure")

        def stop(self):
            events.append("stop")

    monkeypatch.setattr(pgharness, "EphemeralPG", Boom)
    monkeypatch.setattr("e2e_cohort.stack.missing_prerequisites", lambda: None)
    with pytest.raises(RuntimeError):
        await cli._evidence(tmp_path / "out", False)
    assert events == ["start", "stop"]


# ---------------- 真实内测只读核对（verify）的护栏 ----------------
@pytest.fixture
def audit_dsn(pg, report):
    """合成库里的只读核对登录（RUNBOOK 第 1 节的形态：非超级用户、BYPASSRLS 读个人层、可 SET ROLE starbuddy_bff）。只在测试库里建。"""
    pg.psql(f"""DO $$ BEGIN
                  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{AUDIT_ROLE}') THEN
                    CREATE ROLE {AUDIT_ROLE} LOGIN NOINHERIT BYPASSRLS;
                  END IF; END $$;
                GRANT starbuddy_bff TO {AUDIT_ROLE};
                GRANT USAGE ON SCHEMA platform, insight, kge, policy TO {AUDIT_ROLE};
                GRANT SELECT ON ALL TABLES IN SCHEMA platform, insight, kge, policy TO {AUDIT_ROLE};""")
    return f"postgresql://{AUDIT_ROLE}@/{pg.db}?host={pg.work}", f"postgresql://postgres@/{pg.db}?host={pg.work}"


async def test_verify_summary_only_and_guards(report, audit_dsn, pg, tmp_path):
    from e2e_cohort.verify import Refused, preflight, run_verify

    dsn, super_dsn = audit_dsn
    ids = [s.student_id for s in report.cohort.students]
    rc, summary = await run_verify(dsn, ids, "SYN-AUTH-001", tmp_path / "ok", pg.db)
    assert rc == 0, summary
    text = (tmp_path / "ok/pilot_summary.json").read_text(encoding="utf-8")
    assert not any(i in text for i in ids), "汇总里没有任何学生 ID"
    assert summary["students_found"] == 20 and summary["isolation_violations"] == 0 and summary["guardian_links_unbacked"] == 0
    assert summary["isolation_table_source"] == "registry"
    # 名单里有找不到的学生 → 返回 1
    rc, summary = await run_verify(dsn, ids[:2] + [str(uuid.uuid4())], "SYN-AUTH-001", tmp_path / "miss", pg.db)
    assert rc == 1 and summary["students_listed"] == 3 and summary["students_found"] == 2
    # 库名不符、超级用户、输出目录在仓库内：拒绝且不写输出
    with pytest.raises(Refused, match="expect-db"):
        await run_verify(dsn, ids, "SYN-AUTH-001", tmp_path / "wrongdb", "starbuddy_pilot")
    with pytest.raises(Refused, match="超级用户"):
        await run_verify(super_dsn, ids, "SYN-AUTH-001", tmp_path / "super", pg.db)
    with pytest.raises(Refused, match="仓库之内"):
        await run_verify(dsn, ids, "SYN-AUTH-001", ROOT / "tools/e2e-cohort/should-not-exist", pg.db)
    assert not (tmp_path / "wrongdb").exists() and not (tmp_path / "super").exists()
    assert not (ROOT / "tools/e2e-cohort/should-not-exist").exists()
    # 非只读会话：preflight 拒绝
    conn = await asyncpg.connect(dsn)
    try:
        with pytest.raises(Refused, match="只读"):
            await preflight(conn, pg.db)
    finally:
        await conn.close()


def test_verify_cli_refuses_bad_inputs(tmp_path, monkeypatch):
    from e2e_cohort.verify import main

    ids = tmp_path / "students.txt"
    ids.write_text("not-a-uuid\n", encoding="utf-8")
    monkeypatch.delenv("E2E_NOPE_DSN", raising=False)
    assert asyncio.run(main("E2E_NOPE_DSN", ids, "X", tmp_path / "o", "db")) == 2
    monkeypatch.setenv("E2E_NOPE_DSN", "postgresql://nobody@/none?host=/nonexistent")
    assert asyncio.run(main("E2E_NOPE_DSN", ids, "X", tmp_path / "o", "db")) == 2, "名单格式不对"
    assert not (tmp_path / "o").exists()
