"""KGE-04 第二步 PR 3：洞察服务的真实验证任务调度与真实掌握行来源（specs/kge-04-mastery-step2/spec.md 第 4 节）。

一次性 Postgres（会话级 sie03db，全部迁移 0001–0037）上：
- DbMasterySource 直接读 platform.mastery_estimate 的节点键当前行（0028 授权），只认当前已发布地图里的节点与层次，映射成 MasteryRow；
  insight.predict（create_app 缺省来源）有节点行时给出区间；
- LearningVerificationScheduler 经进程内 HTTP 调**真实学习服务**（learning.app.create_app，learning_app 角色连同一个库）的
  `POST /internal/v1/verification`：“我其实会”产生真实的 verify_self_report 任务（platform.retest_task），掌握记录不变；
  发给学习服务的学生取异议记录（insight.dispute.student_id），请求体符合 learning-internal 1.1.0；学生由请求体伪造时 404、不建任务；
  指标里真实任务与接口桩分开计数。
夹具：以超级用户在 replica 模式写合成地图（数学，两个节点）与 S4 的节点键掌握行（只在一次性库），测试结束删掉。全部数据合成（规则 8）。
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import asyncpg
import httpx
import pytest

from insight.dispute import (LearningVerificationScheduler, StubVerificationScheduler, UnavailableVerificationScheduler,
                             VerificationUnavailable, default_scheduler, task_item_ref)
from insight.predict import DbMasterySource
from insight.service.config import Settings
from insight import jobs
from insight.store import InsightStore, create_pool
from pginsight import APP_ROLE, LEARNING_ROLE
from sie03_seed import E4, S4, S5, reset
from support import NODE_A, NODE_B, call, running_app
from test_db import db_now
from test_sie03_db import P, P3, VERIFIER, C, Ops, claim_of, dispute_body, exam_body, plan_exam, q, shanghai_today, stu, sync

ROOT = Path(__file__).resolve().parents[3]
for p in (ROOT / "services" / "learning", ROOT / "packages" / "contracts" / "python"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from learning.app import create_app as learning_app  # noqa: E402
from learning.config import Settings as LearningSettings  # noqa: E402
from learning.contract import internal_contract  # noqa: E402
from learning.mastery import parse_config  # noqa: E402
from learning.mastery.config import CONFIG_DIR  # noqa: E402
from learning.mastery.recompute import MasteryEngine  # noqa: E402

pytestmark = pytest.mark.anyio
MAP = "5b8e1c52-0000-4000-8000-00000000d004"
OLD_NODE = "MATH.SYN.EQ.OLD"                     # 不在当前地图里（改名后的旧单元）
M_A = "6d400000-0000-4000-8000-0000000000a1"     # S4 · NODE_A · apply 的节点键当前行
M_OLD = "6d400000-0000-4000-8000-0000000000a2"   # S4 · OLD_NODE（当前行，但节点不在当前地图里）
M_B_OLD = "6d400000-0000-4000-8000-0000000000a3"  # S4 · NODE_B · apply（已被取代，不是当前行）
M_B_KNOW = "6d400000-0000-4000-8000-0000000000a4"
M_A2 = "6d400000-0000-4000-8000-0000000000a5"    # S4 · NODE_A · apply 验证后的新当前行（结案测试里写入）  # S4 · NODE_B · know（当前行，但层次不在节点的认知层次里）
ALGO = "kge-mastery@0.1.0.syn-0.1.0.0a1b2c3d"
INSIGHT_TOKEN = "synthetic-insight-to-learning-credential-kge04-01"
LEARNING_BFF_TOKEN = "synthetic-bff-to-learning-credential-kge04-0001"

SEED = f"""
SET session_replication_role = replica;
INSERT INTO kge.map_version (id, subject, standard_year, version, status, published_at) VALUES
  ('{MAP}', 'aya', 2022, '1.0.0', 'published', now() - interval '30 days');
INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, cognitive_levels) VALUES
  ('{MAP}', 'MATH.SYN', 'domain', NULL, NULL, '（合成）领域', '{{}}'),
  ('{MAP}', 'MATH.SYN.EQ', 'topic', 'MATH.SYN', NULL, '（合成）主题', '{{}}'),
  ('{MAP}', '{NODE_A}', 'node', 'MATH.SYN.EQ', 'kp', '（合成）节点 A', '{{apply}}'),
  ('{MAP}', '{NODE_B}', 'node', 'MATH.SYN.EQ', 'kp', '（合成）节点 B', '{{apply}}');
INSERT INTO platform.mastery_estimate (id, student_id, subject, node_id, cognitive_level, map_version_id, state, p_mastery, confidence,
  algorithm_version, evidence_ids, is_current) VALUES
  ('{M_A}', '{S4}', 'aya', '{NODE_A}', 'apply', '{MAP}', 'execute', 0.61, 0.52, '{ALGO}', '{{{E4[0]},{E4[1]}}}', true),
  ('{M_OLD}', '{S4}', 'aya', '{OLD_NODE}', 'apply', '{MAP}', 'stable', 0.8, 0.7, '{ALGO}', '{{{E4[2]}}}', true),
  ('{M_B_KNOW}', '{S4}', 'aya', '{NODE_B}', 'know', '{MAP}', 'understand', 0.3, 0.2, '{ALGO}', '{{{E4[3]}}}', true);
INSERT INTO platform.mastery_estimate (id, student_id, subject, node_id, cognitive_level, map_version_id, state, p_mastery, confidence,
  algorithm_version, evidence_ids, is_current, superseded_by, superseded_at) VALUES
  ('{M_B_OLD}', '{S4}', 'aya', '{NODE_B}', 'apply', '{MAP}', 'understand', 0.3, 0.2, '{ALGO}', '{{{E4[3]}}}', false, '{M_B_KNOW}', now());
SET session_replication_role = origin;
"""
CLEAN = f"""
SET session_replication_role = replica;
DELETE FROM platform.retest_task WHERE student_id IN ('{S4}', '{S5}');
DELETE FROM platform.mastery_estimate WHERE id IN ('{M_A2}', '{M_A}', '{M_OLD}', '{M_B_OLD}', '{M_B_KNOW}');
DELETE FROM kge.knowledge_node WHERE map_version_id = '{MAP}';
DELETE FROM kge.map_version WHERE id = '{MAP}';
SET session_replication_role = origin;
"""


@pytest.fixture(scope="module")
def kge04db(sie03db):
    sie03db.psql(CLEAN)
    sie03db.psql(SEED)
    yield sie03db
    reset(sie03db)
    sie03db.psql(CLEAN)


async def _rows(db):
    pool = await create_pool(**db.connect_kwargs(APP_ROLE))
    try:
        return await DbMasterySource(pool).rows(S4, "aya", [NODE_A, NODE_B, OLD_NODE])
    finally:
        await pool.close()


# ---------- 真实掌握行来源 ----------
async def test_db_mastery_source_reads_current_node_rows(kge04db):
    db = kge04db
    rows = await _rows(db)
    # 只有 NODE_A 的当前行：不在当前地图里的旧节点、层次不在节点认知层次里的、已被取代的行都不出现
    assert [(r.basis_id, r.node_id, r.cognitive_level, r.gate, r.subject, r.student_id) for r in rows] == [
        (M_A, NODE_A, "apply", "execute", "aya", S4)]
    r = rows[0]
    assert r.p_mastery == pytest.approx(0.61) and r.confidence == pytest.approx(0.52)
    assert r.evidence_ids == (E4[0], E4[1]) and r.algorithm_version == ALGO and r.computed_at.tzinfo is not None
    pool = await create_pool(**db.connect_kwargs(APP_ROLE))
    try:
        src = DbMasterySource(pool)
        assert await src.rows(S4, "zhixing", [NODE_A]) == ()      # 学科不符
        assert await src.rows(S5, "aya", [NODE_A]) == ()          # 别的学生
        assert await src.rows(S4, "aya", ["not a node"]) == ()    # 非法节点 ID 不进查询
    finally:
        await pool.close()


async def test_predict_gives_interval_from_node_rows(kge04db):
    db = kge04db
    reset(db)
    rows = await _rows(db)
    await sync(db, rows)
    tomorrow = await shanghai_today(db) + timedelta(days=1)
    await plan_exam(db, "monthly", tomorrow, 100, [NODE_A, NODE_B])
    async with running_app(db, verifier=VERIFIER, sie03=P3) as client:   # 不注入：create_app 缺省读节点键当前行
        r = await call(client, "insight.predict", exam_body("syn-kge04-predict-0001", "monthly", tomorrow, 100, [NODE_A, NODE_B]))
        assert r.status_code == 200, r.text
        out = r.json()
        assert C.validate("InsightPredictResponse", out) == []
        assert out["result"]["basis"] == {"node_count": 2, "evidence_count": 2}
        assert set(out["evidence_ids"]) == {E4[0], E4[1]}


# ---------- 真实验证任务调度 ----------
class Recorder(httpx.AsyncBaseTransport):
    """原样转给学习服务的 ASGI 应用，记下请求体（只在内存里，供断言）"""

    def __init__(self, app) -> None:
        self.inner = httpx.ASGITransport(app=app)
        self.bodies: list[dict] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        return await self.inner.handle_async_request(request)


async def _learning(db):
    pool = await asyncpg.create_pool(**db.connect_kwargs(LEARNING_ROLE), min_size=1, max_size=4)
    cfg = json.loads((CONFIG_DIR / "synthetic-test.json").read_text(encoding="utf-8"))
    engine = MasteryEngine(pool, {"aya": parse_config(cfg, env="test")})
    app = learning_app(LearningSettings(env="test", database_url="unused", service_credential=LEARNING_BFF_TOKEN,
                                        insight_client_credential=INSIGHT_TOKEN), pool=pool, mastery=engine)
    return pool, app


async def test_i_know_creates_real_verification_task(kge04db):
    db = kge04db
    reset(db)
    await sync(db, await _rows(db))
    cid = claim_of(db, NODE_A)
    before = q(db, f"SELECT string_agg(id::text || state::text || is_current::text, ',' ORDER BY id) FROM platform.mastery_estimate "
                   f"WHERE student_id = '{S4}'")
    lpool, lapp = await _learning(db)
    try:
        async with lapp.router.lifespan_context(lapp):
            rec = Recorder(lapp)
            scheduler = LearningVerificationScheduler("http://learning.test", INSIGHT_TOKEN, transport=rec)
            since = await db_now(db) - timedelta(minutes=1)
            async with running_app(db, verifier=VERIFIER, sie03=P3, scheduler=scheduler) as client:
                # 学生由请求体伪造（判断属于 S4、请求说是 S5）：404，不调学习服务、不建任务
                forged = await call(client, "insight.dispute", dispute_body(stu("syn-kge04-forge-0001", student=S5), cid, "i_know"), "bff")
                assert forged.status_code == 404 and rec.bodies == []
                r = await call(client, "insight.dispute", dispute_body(stu("syn-kge04-iknow-0001"), cid, "i_know"), "bff")
                assert r.status_code == 200, r.text
                out = r.json()
                assert C.validate("InsightDisputeResponse", out) == []
                res = out["result"]
                did = res["dispute_id"]
                # 发给学习服务的请求：学生取异议记录、层次取判断、符合 learning-internal 1.1.0
                assert len(rec.bodies) == 1
                sent = rec.bodies[0]
                assert internal_contract("1.0.0").validate("VerificationRequest", sent) == []
                assert (sent["student_ref"], sent["dispute_ref"], sent["node_id"], sent["cognitive_level"], sent["item_count"]) == (
                    q(db, f"SELECT student_id FROM insight.dispute WHERE id = '{did}'"), did, NODE_A, "apply", 2)
                # 真实任务：platform.retest_task 一条 verify_self_report，异议记下它的 ID；题目引用是任务引用（不是题目）
                task = q(db, f"SELECT id::text || '|' || student_id || '|' || reason || '|' || node_id || '|' || cognitive_level || '|' || status "
                             f"FROM platform.retest_task WHERE student_id = '{S4}'")
                task_id = task.split("|")[0]
                assert task == f"{task_id}|{S4}|verify_self_report|{NODE_A}|apply|open"
                assert q(db, f"SELECT array_to_string(verification_task_refs, ',') FROM insight.dispute WHERE id = '{did}'") == task_id
                assert res["verification_item_refs"] == [task_item_ref("aya", task_id)]
                assert res["next_step"] == "verification_scheduled"
                # 换 key 再提：库里返回同一条未结束异议，不再调学习服务
                again = await call(client, "insight.dispute", dispute_body(stu("syn-kge04-iknow-0002"), cid, "i_know"), "bff")
                assert again.status_code == 200 and again.json()["result"] == res and len(rec.bodies) == 1
    finally:
        await lpool.close()
    # 不改掌握：掌握记录与之前完全相同
    after = q(db, f"SELECT string_agg(id::text || state::text || is_current::text, ',' ORDER BY id) FROM platform.mastery_estimate "
                  f"WHERE student_id = '{S4}'")
    assert after == before
    assert q(db, f"SELECT status FROM insight.claim WHERE id = '{cid}'") == "to_verify"
    # 指标：真实任务计入 verification_scheduled，不算接口桩
    async with Ops(db) as ops:
        m = await ops.dispute_metrics(since)
    assert m["verification_scheduled"] >= 1 and m["stub_scheduled"] == 0


async def test_i_know_unavailable_unit_rolls_back(kge04db):
    """学习服务 409（单元不在当前地图或学科没有配置）：异议整笔回滚，判断不转待验证，回 409"""
    db = kge04db
    reset(db)
    await sync(db, await _rows(db))
    cid = claim_of(db, NODE_A)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"type": "/problems/mastery_unavailable", "title": "x", "status": 409,
                                         "code": "MASTERY_UNAVAILABLE", "retryable": False})

    scheduler = LearningVerificationScheduler("http://learning.test", INSIGHT_TOKEN, transport=httpx.MockTransport(handler))
    async with running_app(db, verifier=VERIFIER, sie03=P3, scheduler=scheduler) as client:
        r = await call(client, "insight.dispute", dispute_body(stu("syn-kge04-unavail-0001"), cid, "i_know"), "bff")
        assert r.status_code == 409 and C.problem_errors(r.json()) == []
    assert q(db, f"SELECT count(*) FROM insight.dispute WHERE claim_id = '{cid}'") == "0"
    assert q(db, f"SELECT status FROM insight.claim WHERE id = '{cid}'") != "to_verify"


# ---------- 纯单元：配置与缺省选择 ----------
async def test_scheduler_http_errors_and_shape():
    calls = []

    def ok(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        assert request.headers["authorization"] == f"Bearer {INSIGHT_TOKEN}"
        b = calls[-1]
        return httpx.Response(200, json={"task_id": "00000000-0000-4000-8000-00000000d601", "node_id": b["node_id"],
                                         "cognitive_level": b["cognitive_level"], "status": "open", "due_at": "2026-10-09T08:00:00+00:00",
                                         "item_count": b["item_count"], "created": True})

    s = LearningVerificationScheduler("http://learning.test", INSIGHT_TOKEN, transport=httpx.MockTransport(ok))
    dispute = str(uuid.uuid4())
    tasks = await s.schedule(S4.upper(), "aya", NODE_A, dispute, 3, cognitive_level="apply")
    assert [(t.task_id, t.item_ref) for t in tasks] == [("00000000-0000-4000-8000-00000000d601",
                                                         "aya:verify-task-0000000000004000800000000000d601")]
    assert calls[0]["student_ref"] == S4 and calls[0]["item_count"] == 3 and calls[0]["reason"] == "verify_self_report"
    assert await s.describe("aya", ["00000000-0000-4000-8000-00000000d601"]) == [tasks[0].item_ref]
    with pytest.raises(VerificationUnavailable):
        await s.schedule(S4, "aya", NODE_A, dispute, 2)                       # 判断没有层次
    for status in (500, 401):
        bad = LearningVerificationScheduler("http://learning.test", INSIGHT_TOKEN,
                                            transport=httpx.MockTransport(lambda r, st=status: httpx.Response(st)))
        with pytest.raises(RuntimeError):
            await bad.schedule(S4, "aya", NODE_A, dispute, 2, cognitive_level="apply")
    wrong = LearningVerificationScheduler("http://learning.test", INSIGHT_TOKEN, transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"task_id": str(uuid.uuid4()), "node_id": NODE_B, "cognitive_level": "apply"})))
    with pytest.raises(RuntimeError):
        await wrong.schedule(S4, "aya", NODE_A, dispute, 2, cognitive_level="apply")   # 回来的单元对不上


def test_default_scheduler_and_settings():
    assert isinstance(default_scheduler("test"), StubVerificationScheduler)
    assert isinstance(default_scheduler("test", ("http://127.0.0.1:8788", INSIGHT_TOKEN)), StubVerificationScheduler)
    assert isinstance(default_scheduler("dev"), UnavailableVerificationScheduler)
    real = default_scheduler("dev", ("http://127.0.0.1:8788", INSIGHT_TOKEN))
    assert isinstance(real, LearningVerificationScheduler) and real.stub is False and real.available is True
    base = {"INSIGHT_ENV": "dev", "INSIGHT_DATABASE_URL": "postgresql://unused", "INSIGHT_TOKEN_BFF": "b" * 40}
    assert Settings.from_env(base).learning is None
    s = Settings.from_env({**base, "INSIGHT_LEARNING_URL": "http://127.0.0.1:8788/", "INSIGHT_LEARNING_TOKEN": INSIGHT_TOKEN})
    assert s.learning == ("http://127.0.0.1:8788", INSIGHT_TOKEN)
    for bad in ({"INSIGHT_LEARNING_URL": "http://127.0.0.1:8788"},                                  # 只设一个
                {"INSIGHT_LEARNING_TOKEN": INSIGHT_TOKEN},
                {"INSIGHT_LEARNING_URL": "http://learning.example:8788", "INSIGHT_LEARNING_TOKEN": INSIGHT_TOKEN},  # 非回环 http
                {"INSIGHT_LEARNING_URL": "https://learning.internal", "INSIGHT_LEARNING_TOKEN": "short"},
                {"INSIGHT_LEARNING_URL": "https://learning.internal", "INSIGHT_LEARNING_TOKEN": "b" * 40}):   # 与调用方令牌相同
        with pytest.raises(RuntimeError):
            Settings.from_env({**base, **bad})


# ---------- 审查修订：补偿撤销、超时、结案接真实掌握 ----------
class TooMany(LearningVerificationScheduler):
    """真实建了任务，但回给洞察服务 6 条（超过 0032 的上限）：洞察事务回滚，触发补偿撤销"""

    async def schedule(self, *a, **kw):
        tasks = await super().schedule(*a, **kw)
        return tasks * 6


async def test_rollback_after_schedule_cancels_task(kge04db):
    db = kge04db
    reset(db)
    db.psql(f"SET session_replication_role = replica; DELETE FROM platform.retest_task WHERE student_id = '{S4}';")
    await sync(db, await _rows(db))
    cid = claim_of(db, NODE_A)
    lpool, lapp = await _learning(db)
    try:
        async with lapp.router.lifespan_context(lapp):
            scheduler = TooMany("http://learning.test", INSIGHT_TOKEN, transport=httpx.ASGITransport(app=lapp))
            async with running_app(db, verifier=VERIFIER, sie03=P3, scheduler=scheduler) as client:
                r = await call(client, "insight.dispute", dispute_body(stu("syn-kge04-rollback-0001"), cid, "i_know"), "bff")
                assert r.status_code == 500
                stats = client.app.state.insight["ops"].stats
                assert stats["verification_cancelled"] == 6 and stats["verification_cancel_failed"] == 0  # 同一条任务撤 6 次，幂等
    finally:
        await lpool.close()
    # 异议整笔回滚；学习服务建的任务已被撤销（不留孤儿）
    assert q(db, f"SELECT count(*) FROM insight.dispute WHERE claim_id = '{cid}'") == "0"
    assert q(db, f"SELECT string_agg(status, ',') FROM platform.retest_task WHERE student_id = '{S4}'") == "cancelled"


async def test_cancel_failure_only_counted():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    s = LearningVerificationScheduler("http://learning.test", INSIGHT_TOKEN, transport=httpx.MockTransport(handler))
    assert await s.cancel(S4, [str(uuid.uuid4()), str(uuid.uuid4())]) == 2      # 不抛出，只返回失败条数


async def test_scheduler_total_timeout():
    import asyncio

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(2)
        return httpx.Response(200, json={})

    s = LearningVerificationScheduler("http://learning.test", INSIGHT_TOKEN, transport=httpx.MockTransport(slow), total_timeout=0.2)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    with pytest.raises(RuntimeError):
        await s.schedule(S4, "aya", NODE_A, str(uuid.uuid4()), 2, cognitive_level="apply")
    assert loop.time() - t0 < 1.5
    assert await s.cancel(S4, [str(uuid.uuid4())]) == 1                          # 撤销超时也只计失败


async def test_sync_from_db_settles_i_know(kge04db):
    """结案接真实掌握：验证后学习服务写出新的当前行（门槛高于判断所述、带异议之后的新证据），jobs.sync_mastery_from_db 读库结案"""
    db = kge04db
    reset(db)
    pool = await create_pool(**db.connect_kwargs(APP_ROLE))
    store = InsightStore(pool)
    try:
        await jobs.sync_mastery_from_db(store, S4, P)
        cid = claim_of(db, NODE_A)
        async with Ops(db) as ops:
            out = await ops.raise_dispute(S4, cid, "i_know", "student", "a4000000-0000-4000-8000-0000000000a4",
                                          StubVerificationScheduler(), P3)
        db.psql(f"""SET session_replication_role = replica;
          UPDATE platform.mastery_estimate SET is_current = false, superseded_by = '{M_A2}', superseded_at = now() WHERE id = '{M_A}';
          INSERT INTO platform.mastery_estimate (id, student_id, subject, node_id, cognitive_level, map_version_id, state, p_mastery,
            confidence, algorithm_version, evidence_ids, is_current, computed_at) VALUES
            ('{M_A2}', '{S4}', 'aya', '{NODE_A}', 'apply', '{MAP}', 'stable', 0.8, 0.7, '{ALGO}', '{{{E4[0]},{E4[1]},{E4[4]}}}', true,
             now() + interval '1 second');""")
        res = await jobs.sync_mastery_from_db(store, S4, P)
        assert res.settled == ((out.dispute_id, "supported"),)
        assert q(db, f"SELECT status || '|' || result_note_code FROM insight.dispute WHERE id = '{out.dispute_id}'") == \
            "resolved_supported|verification_supported"
    finally:
        await pool.close()
        db.psql(f"""SET session_replication_role = replica;
          DELETE FROM platform.mastery_estimate WHERE id = '{M_A2}';
          UPDATE platform.mastery_estimate SET is_current = true, superseded_by = NULL, superseded_at = NULL WHERE id = '{M_A}';""")


# ---------- 周报触发学情档案页编译（KGE-04 第二步 PR 4，待人确认 #6 默认）----------
def _profile_config():
    """学习服务的合成档案页配置，归节改用洞察服务合成策略的模板键（衔接短语取周报一致性检查的占位短语表，只在测试里）"""
    from learning.mastery.profile import parse_profile_config  # noqa: PLC0415
    doc = json.loads((CONFIG_DIR / "profile-page.synthetic.json").read_text(encoding="utf-8"))
    check = json.loads((ROOT / "services" / "insight" / "config" / "weekly-check.synthetic.json").read_text(encoding="utf-8"))
    doc["sections"] = {P.templates["node_mastery"].key: {"by_gate": {"transfer": "mastered", "retain": "mastered"}, "default": "working_on"},
                       P.templates["node_transfer"].key: {"section": "mastered"}}
    doc["connectives"] = {k: check["connective_phrases"][0] for k in doc["connectives"]}
    return parse_profile_config(doc, check, env="test")


async def test_weekly_trigger_compiles_profile_page_once_via_real_learning(kge04db):
    from insight import store_weekly  # noqa: PLC0415
    from insight.profile_pages import LearningProfilePages  # noqa: PLC0415
    from learning.mastery.profile import ProfilePages  # noqa: PLC0415
    db = kge04db
    reset(db)
    db.psql(f"SET session_replication_role = replica; DELETE FROM platform.profile_page WHERE student_id = '{S4}';")
    await sync(db, await _rows(db))
    lpool = await asyncpg.create_pool(**db.connect_kwargs(LEARNING_ROLE), min_size=1, max_size=4)
    pages = ProfilePages(lpool, _profile_config())
    lapp = learning_app(LearningSettings(env="test", database_url="unused", service_credential=LEARNING_BFF_TOKEN,
                                         insight_client_credential=INSIGHT_TOKEN), pool=lpool, profile=pages)
    try:
        async with lapp.router.lifespan_context(lapp):
            trigger = LearningProfilePages("http://learning.test", INSIGHT_TOKEN, transport=httpx.ASGITransport(app=lapp))
            assert await store_weekly.ensure_profile_pages(trigger, S4, P) == (1, 0)
            assert pages.stats["compiled"] == 1                                               # 周报调用：编一次
            assert await store_weekly.ensure_profile_pages(trigger, S4, P) == (1, 0)
            assert pages.stats["compiled"] == 1 and pages.stats["unchanged"] == 1             # 判断没变：不重编
            refs = q(db, f"""SELECT string_agg(i ->> 'claim_id', ',') FROM platform.profile_page p, jsonb_array_elements(p.sections) s,
                               jsonb_array_elements(s -> 'statements') i WHERE p.student_id = '{S4}'""")
            assert refs == claim_of(db, NODE_A)                                               # 陈述引用洞察服务写的判断
            wrong = LearningProfilePages("http://learning.test", LEARNING_BFF_TOKEN, transport=httpx.ASGITransport(app=lapp))
            assert await store_weekly.ensure_profile_pages(wrong, S4, P) == (0, 1)            # BFF 令牌 401：只计数，不抛出
    finally:
        await lpool.close()
        db.psql(f"SET session_replication_role = replica; DELETE FROM platform.profile_page WHERE student_id = '{S4}';")
