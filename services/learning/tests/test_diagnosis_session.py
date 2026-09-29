"""SIE-02a 诊断会话端到端（spec 验收第 1、2、3 条）：学习服务 → 阿鸦适配器（真实进程，判分只来自验证器）→ 一次性库。

合成学生按预设掌握图作答：答案取自阿鸦内容包（验证器自己的数据；合成夹具与题面不带答案），每题用时取合成分布。
探针由测试以迁移执行者身份写入（模拟 BFF 以学生身份调 insight.record_confidence_probe），再把 probe_id 交给学习服务。
没有 node 或阿鸦依赖时跳过（LEARNING_TEST_STRICT=1 时失败）。全部合成数据（规则 8）。
"""
from __future__ import annotations

import json
import random
import uuid
from pathlib import Path

import httpx
import pytest

from conftest import CREDENTIAL
from conftest import settings as make_settings
from learning.app import create_app
from learning.config import AdapterTarget, Settings
from learning.diagnosis.engine import ALGORITHM_VERSION, Done, Params, trace
from learning.diagnosis.source import FixtureSource, load_fixture
from test_diagnosis_engine import BASE, Q, closure, oracle_break_points
from review_loop import close_loop, console
from test_e2e_aya import AYA_CREDENTIAL, aya_adapter  # noqa: F401  （模块级夹具：阿鸦适配器进程）

ROOT = Path(__file__).resolve().parents[3]
PACK = {i["id"]: i for i in json.loads((ROOT / "adapters/aya/content/pack.json").read_text(encoding="utf-8"))["items"]}
NODE_OF = {i["item_id"]: i["node_id"] for i in load_fixture()["items"]}
SRC = FixtureSource("test")


def synthetic_answer(item_id: str, correct: bool) -> dict:
    it = PACK[item_id]
    if it["format"] == "fill":
        return {"text": it["answer"][0] if correct else "-999"}
    if correct:
        return {"choice": list(it["answer"])}
    return {"choice": [next(o["key"] for o in it["options"] if o["key"] not in it["answer"])]}


@pytest.fixture
async def dapi(pools, aya_adapter):  # noqa: F811
    app_pool, _ = pools
    s = make_settings(adapters={"aya": AdapterTarget(aya_adapter, AYA_CREDENTIAL)}, diagnosis_presenter="synthetic")
    app = create_app(s, pool=app_pool)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test", timeout=60) as c:
            c.headers["authorization"] = f"Bearer {CREDENTIAL}"
            yield c


async def add_probe(admin, student: str, item: str) -> str:
    async with admin.acquire() as c:
        return await c.fetchval(
            """INSERT INTO insight.confidence_probe (student_id, subject, item_id, prediction, asked_at, idempotency_key)
               VALUES ($1::uuid, 'aya', $2, 'unsure', now(), gen_random_uuid()) RETURNING id::text""", student, item)


async def counts(admin, student: str) -> dict:
    async with admin.acquire() as c:
        return dict(await c.fetchrow(
            """SELECT (SELECT count(*) FROM platform.evidence_event WHERE student_id = $1::uuid) AS evidence,
                      (SELECT count(*) FROM platform.attempt WHERE student_id = $1::uuid) AS attempts,
                      (SELECT count(*) FROM platform.grade_result WHERE student_id = $1::uuid) AS grades,
                      (SELECT count(*) FROM platform.review_task WHERE student_id = $1::uuid AND source_kind = 'diagnosis_step') AS process_reviews,
                      (SELECT count(*) FROM platform.diagnosis_step st JOIN platform.diagnosis_session s ON s.id = st.session_id
                        WHERE s.student_id = $1::uuid) AS steps""", student))


async def start(api, student: str, key: str | None = None, usage: str | None = None) -> httpx.Response:
    return await api.post("/internal/v1/diagnosis/sessions", json={
        "student_ref": student, "subject": "aya", "usage_id": usage or str(uuid.uuid4()), "idempotency_key": key or str(uuid.uuid4())})


async def play(api, admin, student: str, mastery: set[str], *, seed: int = 11, probe: bool = True, pause_at: int | None = None,
               replay_each: bool = False, stop_at: int | None = None) -> dict:
    """合成学生做完一次会话。pause_at：在这一步作答前暂停、再续做；replay_each：每步作答后原样重放一次。"""
    rng = random.Random(seed)
    r = await start(api, student)
    assert r.status_code == 201, r.text
    sid = r.json()["session_id"]
    seen: list[dict] = []
    for _ in range(80):
        v = (await api.get(f"/internal/v1/diagnosis/sessions/{sid}", params={"student_ref": student})).json()
        assert "answer" not in json.dumps(v) and "reason" not in json.dumps(v) and "node" not in json.dumps(v)
        if v["status"] == "finished":
            return {"session_id": sid, "view": v, "seen": seen}
        step = v["step"]
        if stop_at is not None and step["seq"] >= stop_at:
            return {"session_id": sid, "view": v, "seen": seen}
        seen.append(step)
        if pause_at is not None and step["seq"] == pause_at:
            p = await api.post(f"/internal/v1/diagnosis/sessions/{sid}/pause", json={"student_ref": student, "seq": step["seq"],
                                                                                    "step_elapsed_sec": 12})
            assert p.status_code == 200 and p.json()["status"] == "paused"
            blocked = await api.post(f"/internal/v1/diagnosis/sessions/{sid}/answer", json={
                "student_ref": student, "seq": step["seq"], "answer": synthetic_answer(step["item_id"], True), "elapsed_sec": 30})
            assert blocked.status_code == 409 and blocked.json()["code"] == "INVALID_STATE"
            again = await start(api, student)  # 跨天续做：新的“开始”请求续做同一个会话，不新建、不另占额度
            assert again.status_code == 200 and again.json()["session_id"] == sid and again.json()["created"] is False
            v2 = (await api.get(f"/internal/v1/diagnosis/sessions/{sid}", params={"student_ref": student})).json()
            assert v2["step"]["seq"] == step["seq"] and v2["step"]["step_elapsed_sec"] == 12
        if probe:
            pid = await add_probe(admin, student, step["item_id"])
            pr = await api.post(f"/internal/v1/diagnosis/sessions/{sid}/probe", json={"student_ref": student, "seq": step["seq"],
                                                                                      "probe_id": pid})
            assert pr.status_code == 200, pr.text
        node = NODE_OF[step["item_id"]]
        sec = rng.randint(15, 90) if step["kind"] == "adaptive" else rng.randint(60, 180)
        body = {"student_ref": student, "seq": step["seq"], "answer": synthetic_answer(step["item_id"], node in mastery), "elapsed_sec": sec}
        if step["kind"] != "adaptive":
            body["work"] = f"合成过程第 {step['seq']} 步：先写已知，再列式，最后求解。"
        a = await api.post(f"/internal/v1/diagnosis/sessions/{sid}/answer", json=body)
        assert a.status_code == 200, a.text
        assert set(a.json()) == {"attempt_id", "probe_id", "status", "usage_id"}  # 不回对错
        if replay_each:
            before = await counts(admin, student)
            a2 = await api.post(f"/internal/v1/diagnosis/sessions/{sid}/answer", json=body)
            assert a2.status_code == 200 and a2.headers["idempotent-replayed"] == "true" and a2.json()["attempt_id"] == a.json()["attempt_id"]
            assert await counts(admin, student) == before
    raise AssertionError("session did not finish")


async def db_steps(admin, sid: str) -> list:
    async with admin.acquire() as c:
        return await c.fetch(
            """SELECT st.seq, st.kind, st.node_id, st.item_id::text, st.reason_code, st.status, st.probe_id::text, st.elapsed_sec,
                      g.verdict FROM platform.diagnosis_step st LEFT JOIN platform.grade_result g ON g.id = st.grade_result_id
                WHERE st.session_id = $1::uuid ORDER BY st.seq""", sid)


def as_done(rows) -> list[Done]:
    from learning.diagnosis.engine import outcome_of
    return [Done(r["kind"], r["node_id"], r["item_id"], r["reason_code"], r["status"],
                 outcome_of(r["verdict"]) if r["status"] == "answered" else None, r["elapsed_sec"] or 0) for r in rows]


PRESET = closure(SRC, {Q + "GRAPH_BASIC"}) | BASE  # 卡在顶点式


async def test_synthetic_student_full_session(dapi, pools, student, pg):
    _, admin = pools
    out = await play(dapi, admin, student, PRESET, replay_each=True)
    sid = out["session_id"]
    rows = await db_steps(admin, sid)
    steps = as_done(rows)
    # 判分只来自验证器：答对 / 答错与掌握图一致（阿鸦验证器按内容包判）
    for d in steps:
        if d.status == "answered":
            assert d.outcome == ("correct" if d.node_id in PRESET else "incorrect"), d
    # 断点与预设一致；答对节点的强前置全部跳过；无题节点为 no_item
    t = trace(SRC, 9, steps)
    assert t.finished and set(t.break_points) == oracle_break_points(SRC, SRC.targets(9), PRESET)
    for i, d in enumerate(steps):
        if d.kind == "adaptive" and d.outcome == "correct":
            assert not ({x.node_id for x in steps[i + 1:] if x.kind == "adaptive"} & SRC.ancestors(d.node_id))
        if d.reason == "no_item":
            assert d.item_id is None and not SRC.items(d.node_id)
    # 题数与用时在上限内；会话记下算法版本与额度占用
    async with admin.acquire() as c:
        s = await c.fetchrow("""SELECT status, algorithm_version::text, map_version, item_count, elapsed_sec, usage_id::text, finished_at
                                  FROM platform.diagnosis_session WHERE id = $1::uuid""", sid)
    assert s["status"] == "finished" and s["algorithm_version"] == ALGORITHM_VERSION and s["finished_at"] is not None
    answered = [d for d in steps if d.status == "answered"]
    assert s["item_count"] == len(answered) <= 20 and s["elapsed_sec"] == sum(d.elapsed_sec for d in answered) <= 2100
    # 每题前有探针（都记到了步骤上）；过程观察作答进复核队列
    assert all(r["probe_id"] for r in rows if r["status"] == "answered")
    c_ = await counts(admin, student)
    proc = [d for d in steps if d.kind in ("process", "explain")]
    assert len(proc) == 4 and c_["process_reviews"] == len(proc)
    # 证据：每道作答 2 条（阿鸦：作答 + 验证器轨迹），过程题另有 1 条书写原文
    assert c_["attempts"] == c_["grades"] == len(answered) and c_["evidence"] == 2 * len(answered) + len(proc)
    # 与纯函数模拟的序列一致（同一规则）
    from test_diagnosis_engine import simulate
    sim, _ = simulate(SRC, PRESET)
    assert [(x.kind, x.node_id, x.item_id, x.reason) for x in sim] == [(x.kind, x.node_id, x.item_id, x.reason) for x in steps]
    # 结束后：再答、再暂停都拒绝；诊断不产生对学生可见的结论（视图里没有对错与断点）
    assert out["view"]["step"] is None and set(out["view"]) == {"session_id", "status", "state", "usage_id", "elapsed_sec",
                                                                 "time_budget_sec", "item_count", "item_limit", "step"}
    r = await dapi.post(f"/internal/v1/diagnosis/sessions/{sid}/pause", json={"student_ref": student})
    assert r.status_code == 409
    # 过程观察在审核台走完“进队 → 领取 → 结案”（只能用“已看过程并记录”或“无需处理”，不能用判分的结案码）
    async with admin.acquire() as c:
        step_id = await c.fetchval("""SELECT id::text FROM platform.diagnosis_step WHERE session_id = $1::uuid AND kind = 'process'
                                       ORDER BY seq LIMIT 1""", sid)
    async with console(pg) as client:
        done = await close_loop(client, source_id=step_id, student_id=student, subject="aya", expect_reasons={"process_observation"},
                                code="process_noted", wrong_code="upheld")
    assert done["source"]["kind"] == "process" and "reason_code" not in done["source"] and done["source"]["evidence_ids"]


async def test_same_input_twice_same_sequence_and_pause_resume(dapi, pools):
    """两个合成学生同样的掌握图：选题序列逐字节相同；其中一个中途暂停、跨天续做，结果不变。"""
    _, admin = pools
    seqs = []
    for pause in (None, 4):
        stu = await new_student(admin)
        out = await play(dapi, admin, stu, PRESET, pause_at=pause, probe=pause is None)
        rows = await db_steps(admin, out["session_id"])
        seqs.append(json.dumps([[r["kind"], r["node_id"], r["item_id"], r["reason_code"], r["status"]] for r in rows],
                               ensure_ascii=False).encode())
    assert seqs[0] == seqs[1]


async def new_student(admin) -> str:
    acct, guardian, household, sid = (str(uuid.uuid4()) for _ in range(4))
    async with admin.acquire() as c:
        await c.execute("INSERT INTO platform.account (id, kind) VALUES ($1::uuid, 'guardian'), ($2::uuid, 'student')", guardian, acct)
        await c.execute("INSERT INTO platform.household (id, created_by_account) VALUES ($1::uuid, $2::uuid)", household, guardian)
        await c.execute("""INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month)
                           VALUES ($1::uuid, $2::uuid, $3::uuid, 9, 'nanjing', 'suke-2024', '2012-03-01')""", sid, acct, household)
    return sid


async def test_idempotent_start_probe_and_answer(dapi, pools, student):
    _, admin = pools
    key, usage = str(uuid.uuid4()), str(uuid.uuid4())
    r1 = await start(dapi, student, key, usage)
    r2 = await start(dapi, student, key, usage)
    assert r1.status_code == 201 and r2.status_code == 200 and r2.headers["idempotent-replayed"] == "true"
    assert r1.json()["session_id"] == r2.json()["session_id"] and r2.json()["usage_id"] == usage
    # 同一键换 usage_id：冲突
    assert (await start(dapi, student, key, str(uuid.uuid4()))).status_code == 409
    sid = r1.json()["session_id"]
    v = (await dapi.get(f"/internal/v1/diagnosis/sessions/{sid}", params={"student_ref": student})).json()
    step = v["step"]
    assert step["seq"] == 1 and step["kind"] == "adaptive" and v["state"] == "probing"
    # 探针：别的题的探针拒绝；同一探针重放 ok；换一条探针冲突
    other = next(i for i in NODE_OF if i != step["item_id"])
    bad = await add_probe(admin, student, other)
    r = await dapi.post(f"/internal/v1/diagnosis/sessions/{sid}/probe", json={"student_ref": student, "seq": 1, "probe_id": bad})
    assert r.status_code == 422 and r.json()["code"] == "PROBE_MISMATCH"
    pid = await add_probe(admin, student, step["item_id"])
    for replay in ("false", "true"):
        r = await dapi.post(f"/internal/v1/diagnosis/sessions/{sid}/probe", json={"student_ref": student, "seq": 1, "probe_id": pid})
        assert r.status_code == 200 and r.headers["idempotent-replayed"] == replay
    pid2 = await add_probe(admin, student, step["item_id"])
    r = await dapi.post(f"/internal/v1/diagnosis/sessions/{sid}/probe", json={"student_ref": student, "seq": 1, "probe_id": pid2})
    assert r.status_code == 409
    v = (await dapi.get(f"/internal/v1/diagnosis/sessions/{sid}", params={"student_ref": student})).json()
    assert v["state"] == "awaiting_answer"
    # 作答：格式不对 422（不送验证器）；过程说明只给过程题
    bad_body = {"student_ref": student, "seq": 1, "answer": {"choice": ["Z"]}, "elapsed_sec": 20}
    assert (await dapi.post(f"/internal/v1/diagnosis/sessions/{sid}/answer", json=bad_body)).status_code == 422
    work_body = {"student_ref": student, "seq": 1, "answer": synthetic_answer(step["item_id"], True), "elapsed_sec": 20, "work": "x"}
    assert (await dapi.post(f"/internal/v1/diagnosis/sessions/{sid}/answer", json=work_body)).status_code == 422
    assert (await counts(admin, student))["attempts"] == 0
    body = {"student_ref": student, "seq": 1, "answer": synthetic_answer(step["item_id"], True), "elapsed_sec": 20}
    a1 = await dapi.post(f"/internal/v1/diagnosis/sessions/{sid}/answer", json=body)
    before = await counts(admin, student)
    a2 = await dapi.post(f"/internal/v1/diagnosis/sessions/{sid}/answer", json=body)
    assert a1.status_code == a2.status_code == 200 and a2.headers["idempotent-replayed"] == "true"
    assert a1.json()["attempt_id"] == a2.json()["attempt_id"] and a1.json()["probe_id"] == pid
    assert await counts(admin, student) == before
    # 作答发生时间取探针写入时间：2-L 的关联时间窗成立
    async with admin.acquire() as c:
        ok = await c.fetchval("""SELECT a.occurred_at = p.created_at FROM platform.attempt a, insight.confidence_probe p
                                  WHERE a.attempt_id = $1::uuid AND p.id = $2::uuid""", a1.json()["attempt_id"], pid)
    assert ok
    # 旧的一步不能再答（不是当前步）
    stale = dict(body, seq=5)
    assert (await dapi.post(f"/internal/v1/diagnosis/sessions/{sid}/answer", json=stale)).status_code == 409


@pytest.mark.parametrize("resume_by", ["current", "answer"])
async def test_recovery_between_grading_and_step_update(dapi, pools, student, monkeypatch, resume_by):
    """判分成功、步骤还没推进时中断：重放同一作答不再判第二次（不重复写证据）；取当前题也会用已记录的判分推进。"""
    _, admin = pools
    from learning.diagnosis import store as dstore
    sid = (await start(dapi, student)).json()["session_id"]
    step = (await dapi.get(f"/internal/v1/diagnosis/sessions/{sid}", params={"student_ref": student})).json()["step"]
    body = {"student_ref": student, "seq": 1, "answer": synthetic_answer(step["item_id"], False), "elapsed_sec": 33}
    real = dstore.answer_step

    async def boom(*a, **k):
        raise RuntimeError("synthetic crash after grading")
    monkeypatch.setattr(dstore, "answer_step", boom)
    r = await dapi.post(f"/internal/v1/diagnosis/sessions/{sid}/answer", json=body)
    assert r.status_code == 500 and r.json()["retryable"] is True
    monkeypatch.setattr(dstore, "answer_step", real)
    mid = await counts(admin, student)
    assert mid["grades"] == 1 and mid["steps"] == 1  # 判分已落库，步骤没推进
    if resume_by == "current":
        v = (await dapi.get(f"/internal/v1/diagnosis/sessions/{sid}", params={"student_ref": student})).json()
        assert v["step"]["seq"] == 2 and v["item_count"] == 1
        assert (await counts(admin, student))["evidence"] == mid["evidence"]
    r = await dapi.post(f"/internal/v1/diagnosis/sessions/{sid}/answer", json=body)
    assert r.status_code == 200 and r.headers["idempotent-replayed"] == "true"
    after = await counts(admin, student)
    assert after["grades"] == 1 and after["evidence"] == mid["evidence"] and after["attempts"] == 1
    v = (await dapi.get(f"/internal/v1/diagnosis/sessions/{sid}", params={"student_ref": student})).json()
    assert v["step"]["seq"] == 2 and v["item_count"] == 1 and v["elapsed_sec"] == 33


async def test_auth_isolation_abandon_and_config(dapi, pools, student):
    _, admin = pools
    sid = (await start(dapi, student)).json()["session_id"]
    other = await new_student(admin)
    # 没有令牌 401；别的学生读不到（与不存在同样 404）
    r = await dapi.get(f"/internal/v1/diagnosis/sessions/{sid}", params={"student_ref": student}, headers={"authorization": "Bearer nope"})
    assert r.status_code == 401
    for who in (other, "not-a-uuid"):
        r = await dapi.get(f"/internal/v1/diagnosis/sessions/{sid}", params={"student_ref": who})
        assert r.status_code == 404 and r.json()["code"] == "SESSION_NOT_FOUND"
    r = await dapi.get(f"/internal/v1/diagnosis/sessions/{uuid.uuid4()}", params={"student_ref": student})
    assert r.status_code == 404
    # 未知键 400；非阿鸦学科 400
    r = await dapi.post("/internal/v1/diagnosis/sessions", json={"student_ref": student, "subject": "aya", "usage_id": str(uuid.uuid4()),
                                                                 "idempotency_key": str(uuid.uuid4()), "note": "x"})
    assert r.status_code == 400
    r = await dapi.post("/internal/v1/diagnosis/sessions", json={"student_ref": other, "subject": "wenli", "usage_id": str(uuid.uuid4()),
                                                                 "idempotency_key": str(uuid.uuid4())})
    assert r.status_code == 400
    # 放弃：幂等；放弃后可以开新会话
    for replay in ("false", "true"):
        r = await dapi.post(f"/internal/v1/diagnosis/sessions/{sid}/abandon", json={"student_ref": student})
        assert r.status_code == 200 and r.headers["idempotent-replayed"] == replay and r.json()["status"] == "abandoned"
    r = await start(dapi, student)
    assert r.status_code == 201 and r.json()["session_id"] != sid


def test_synthetic_presenter_refused_outside_test_env():
    base = {"LEARNING_SERVICE_TOKEN": "s" * 40, "LEARNING_DATABASE_URL": "postgresql://x", "LEARNING_DIAGNOSIS_PRESENTER": "synthetic"}
    with pytest.raises(RuntimeError):
        Settings.from_env({**base, "LEARNING_ENV": "dev"})
    s = Settings.from_env({**base, "LEARNING_ENV": "test"})
    assert s.diagnosis_presenter == "synthetic" and s.diagnosis_source == "fixture"
    with pytest.raises(RuntimeError):
        Settings.from_env({**base, "LEARNING_ENV": "test", "LEARNING_DIAGNOSIS_PRESENTER": "real"})
    with pytest.raises(RuntimeError):  # 知识服务来源要令牌、地址、目标与地图版本
        Settings.from_env({**base, "LEARNING_ENV": "test", "LEARNING_DIAGNOSIS_SOURCE": "knowledge"})
    s = Settings.from_env({**base, "LEARNING_ENV": "test", "LEARNING_DIAGNOSIS_SOURCE": "knowledge",
                           "LEARNING_KNOWLEDGE_URL": "http://127.0.0.1:8790", "LEARNING_KNOWLEDGE_TOKEN": "k" * 40,
                           "LEARNING_DIAGNOSIS_TARGETS": '{"9": ["MATH.FUNC.QUAD.APPLY"]}', "LEARNING_DIAGNOSIS_MAP_VERSION": "0.1.0"})
    assert s.knowledge is not None and s.diagnosis_targets == {9: ["MATH.FUNC.QUAD.APPLY"]}
    with pytest.raises(RuntimeError):
        Settings.from_env({**base, "LEARNING_ENV": "test", "LEARNING_DIAGNOSIS_SOURCE": "knowledge",
                           "LEARNING_KNOWLEDGE_URL": "http://10.0.0.8:8790", "LEARNING_KNOWLEDGE_TOKEN": "k" * 40,
                           "LEARNING_DIAGNOSIS_TARGETS": '{"9": []}', "LEARNING_DIAGNOSIS_MAP_VERSION": "0.1.0"})


async def test_diagnosis_unavailable_without_presenter(api, student):
    """没配合成题面（例如 dev 环境）：诊断接口一律 501，不出题。"""
    r = await api.post("/internal/v1/diagnosis/sessions", json={"student_ref": student, "subject": "aya", "usage_id": str(uuid.uuid4()),
                                                                 "idempotency_key": str(uuid.uuid4())})
    assert r.status_code == 501 and r.json()["code"] == "DIAGNOSIS_UNAVAILABLE"


def test_params_default_match_migration():
    p = Params()
    assert (p.item_limit, p.time_budget_sec) == (20, 2100)  # 0027 的缺省值
