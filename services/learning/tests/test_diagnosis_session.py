"""SIE-02a 诊断会话端到端（spec 验收第 1、2、3 条）：学习服务 → 阿鸦适配器（真实进程，判分只来自验证器）→ 一次性库。

合成学生按预设掌握图作答：答案取自阿鸦内容包（验证器自己的数据；合成夹具与题面不带答案），每题用时取合成分布。
探针由测试以迁移执行者身份写入（模拟 BFF 以学生身份调 insight.record_confidence_probe），再把 probe_id 交给学习服务。
没有 node 或阿鸦依赖时跳过（LEARNING_TEST_STRICT=1 时失败）。全部合成数据（规则 8）。
"""
from __future__ import annotations

import json
import random
import uuid
from datetime import datetime, timedelta, timezone
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


@pytest.fixture(scope="module")
def aya_v1(request):
    """诊断作答走 v1 写入路径（候选契约 1.0.0）：本模块内学习服务与阿鸦适配器都开候选版（只在测试里）。"""
    mp = pytest.MonkeyPatch()
    mp.setenv("STARBUDDY_ALLOW_CANDIDATE_CONTRACT", "1")
    try:
        yield request.getfixturevalue("aya_adapter")
    finally:
        mp.undo()


@pytest.fixture
async def dapi(pools, aya_v1):
    app_pool, _ = pools
    s = make_settings(adapters={"aya": AdapterTarget(aya_v1, AYA_CREDENTIAL)}, diagnosis_presenter="synthetic")
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


async def backdate(admin, sid: str, seq: int, seconds: int) -> None:
    """把这一步的出题时间往前挪（模拟学生确实在这道题上花了这么久；服务端按墙钟给用时封顶，S1）。"""
    async with admin.acquire() as c, c.transaction():
        # 只在测试库：绕过 0027 的“续做时间只增不减”守卫，把会话的续做时间一起挪到出题时间之前（墙钟起点取两者较晚者）
        await c.execute("SET LOCAL session_replication_role = replica")
        at = await c.fetchval("""UPDATE platform.diagnosis_step SET created_at = created_at - make_interval(secs => $3)
                                  WHERE session_id = $1::uuid AND seq = $2 AND status = 'presented' RETURNING created_at""", sid, seq, seconds)
        await c.execute("UPDATE platform.diagnosis_session SET resumed_at = least(resumed_at, $2) WHERE id = $1::uuid", sid, at)


def cur_view(api, sid: str, student: str):
    return api.get(f"/internal/v1/diagnosis/sessions/{sid}", headers={"x-student-ref": student})


async def play(api, admin, student: str, mastery: set[str], *, seed: int = 11, probe: bool = True, pause_at: int | None = None,
               replay_each: bool = False, stop_at: int | None = None) -> dict:
    """合成学生做完一次会话。pause_at：在这一步作答前暂停、再续做；replay_each：每步作答后原样重放一次，再换键同内容重放一次。"""
    rng = random.Random(seed)
    r = await start(api, student)
    assert r.status_code == 201, r.text
    sid = r.json()["session_id"]
    seen: list[dict] = []
    for _ in range(80):
        v = (await api.get(f"/internal/v1/diagnosis/sessions/{sid}", headers={"x-student-ref": student})).json()
        # 视图顶层的 abandon_reason（会话为何被放弃，只取 null / grade_changed）是允许的字段；其余部分不得带答案、判题理由、节点
        assert v.get("abandon_reason") in (None, "grade_changed")
        rest = json.dumps({k: x for k, x in v.items() if k != "abandon_reason"})
        assert "answer" not in rest and "reason" not in rest and "node" not in rest
        if v["status"] == "finished":
            return {"session_id": sid, "view": v, "seen": seen}
        step = v["step"]
        if stop_at is not None and step["seq"] >= stop_at:
            return {"session_id": sid, "view": v, "seen": seen}
        seen.append(step)
        if pause_at is not None and step["seq"] == pause_at:
            await backdate(admin, sid, step["seq"], 60)  # 看题 60 秒后暂停：上报的 12 秒在墙钟之内，照记
            p = await api.post(f"/internal/v1/diagnosis/sessions/{sid}/pause", json={"student_ref": student, "seq": step["seq"],
                                                                                    "step_elapsed_sec": 12})
            assert p.status_code == 200 and p.json()["status"] == "paused"
            blocked = await api.post(f"/internal/v1/diagnosis/sessions/{sid}/answer", json={
                "student_ref": student, "seq": step["seq"], "answer": synthetic_answer(step["item_id"], True), "elapsed_sec": 30,
                "idempotency_key": str(uuid.uuid4())})
            assert blocked.status_code == 409 and blocked.json()["code"] == "INVALID_STATE"
            again = await start(api, student)  # 跨天续做：新的“开始”请求续做同一个会话，不新建、不另占额度
            assert again.status_code == 200 and again.json()["session_id"] == sid and again.json()["created"] is False
            v2 = (await api.get(f"/internal/v1/diagnosis/sessions/{sid}", headers={"x-student-ref": student})).json()
            assert v2["step"]["seq"] == step["seq"] and v2["step"]["step_elapsed_sec"] == 12
        if probe:
            pid = await add_probe(admin, student, step["item_id"])
            pr = await api.post(f"/internal/v1/diagnosis/sessions/{sid}/probe", json={"student_ref": student, "seq": step["seq"],
                                                                                      "probe_id": pid})
            assert pr.status_code == 200, pr.text
        node = NODE_OF[step["item_id"]]
        sec = rng.randint(15, 90) if step["kind"] == "adaptive" else rng.randint(60, 180)
        await backdate(admin, sid, step["seq"], sec)  # 合成用时在墙钟之内：记入的就是上报值
        body = {"student_ref": student, "seq": step["seq"], "answer": synthetic_answer(step["item_id"], node in mastery), "elapsed_sec": sec,
                "idempotency_key": str(uuid.uuid4())}
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
            # 换一个键、内容相同：仍回原结果（BFF 与前端重试时会换键）；换键且内容不同：冲突（S5）
            a3 = await api.post(f"/internal/v1/diagnosis/sessions/{sid}/answer", json=dict(body, idempotency_key=str(uuid.uuid4())))
            assert a3.status_code == 200 and a3.headers["idempotent-replayed"] == "true" and a3.json()["attempt_id"] == a.json()["attempt_id"]
            other = synthetic_answer(step["item_id"], node not in mastery)
            a4 = await api.post(f"/internal/v1/diagnosis/sessions/{sid}/answer", json=dict(body, answer=other, idempotency_key=str(uuid.uuid4())))
            assert a4.status_code == 409 and a4.json()["code"] == "ANSWER_CONFLICT", a4.text
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
                                                                 "time_budget_sec", "item_count", "item_limit", "process_tasks", "step",
                                                                 "abandon_reason"}
    assert out["view"]["abandon_reason"] is None  # 正常做完的会话没有放弃原因
    assert out["view"]["process_tasks"] is True
    # 作答走 v1 写入路径：键盘通道、证据等级按默认推导（A）；过程原文证据准入固定 pending_review，引用记在步骤上（契约 B1）
    async with admin.acquire() as c:
        ev = await c.fetch("""SELECT e.kind, e.channel, e.evidence_level, e.admission::text, e.evidence_id::text,
                                     e.evidence_id IN (SELECT work_evidence_id FROM platform.diagnosis_step WHERE session_id = $2::uuid) AS work
                                FROM platform.evidence_event e WHERE e.student_id = $1::uuid""", student, sid)
        work_rows = await c.fetch("""SELECT kind, work_evidence_id::text FROM platform.diagnosis_step
                                      WHERE session_id = $1::uuid AND status = 'answered' ORDER BY seq""", sid)
    graded = [e for e in ev if not e["work"]]
    assert graded and all(e["channel"] == "keypad" and e["evidence_level"] == "A" for e in graded)
    works = [e for e in ev if e["work"]]
    assert len(works) == len(proc) and all(e["kind"] == "response_text" and e["admission"] == "pending_review" for e in works)
    assert all((r["work_evidence_id"] is not None) == (r["kind"] != "adaptive") for r in work_rows)
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
    # 审核台详情给出过程原文证据的引用（审核台角色读不到证据表，原文显示“待授权”，lessons 待人确认）
    async with admin.acquire() as c:
        wid = await c.fetchval("SELECT work_evidence_id::text FROM platform.diagnosis_step WHERE id = $1::uuid", step_id)
    assert wid and done["source"]["work_evidence_id"] == wid


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
    v = (await dapi.get(f"/internal/v1/diagnosis/sessions/{sid}", headers={"x-student-ref": student})).json()
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
    v = (await dapi.get(f"/internal/v1/diagnosis/sessions/{sid}", headers={"x-student-ref": student})).json()
    assert v["state"] == "awaiting_answer"
    # 作答：格式不对 422（不送验证器）；过程说明只给过程题
    bad_body = {"student_ref": student, "seq": 1, "answer": {"choice": ["Z"]}, "elapsed_sec": 20, "idempotency_key": str(uuid.uuid4())}
    assert (await dapi.post(f"/internal/v1/diagnosis/sessions/{sid}/answer", json=bad_body)).status_code == 422
    work_body = {"student_ref": student, "seq": 1, "answer": synthetic_answer(step["item_id"], True), "elapsed_sec": 20, "work": "x", "idempotency_key": str(uuid.uuid4())}
    assert (await dapi.post(f"/internal/v1/diagnosis/sessions/{sid}/answer", json=work_body)).status_code == 422
    assert (await counts(admin, student))["attempts"] == 0
    body = {"student_ref": student, "seq": 1, "answer": synthetic_answer(step["item_id"], True), "elapsed_sec": 20, "idempotency_key": str(uuid.uuid4())}
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
    step = (await dapi.get(f"/internal/v1/diagnosis/sessions/{sid}", headers={"x-student-ref": student})).json()["step"]
    body = {"student_ref": student, "seq": 1, "answer": synthetic_answer(step["item_id"], False), "elapsed_sec": 33,
            "idempotency_key": str(uuid.uuid4())}
    await backdate(admin, sid, 1, 40)
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
        v = (await dapi.get(f"/internal/v1/diagnosis/sessions/{sid}", headers={"x-student-ref": student})).json()
        assert v["step"]["seq"] == 2 and v["item_count"] == 1
        assert (await counts(admin, student))["evidence"] == mid["evidence"]
    r = await dapi.post(f"/internal/v1/diagnosis/sessions/{sid}/answer", json=body)
    assert r.status_code == 200 and r.headers["idempotent-replayed"] == "true"
    after = await counts(admin, student)
    assert after["grades"] == 1 and after["evidence"] == mid["evidence"] and after["attempts"] == 1
    v = (await dapi.get(f"/internal/v1/diagnosis/sessions/{sid}", headers={"x-student-ref": student})).json()
    assert v["step"]["seq"] == 2 and v["item_count"] == 1 and v["elapsed_sec"] == 33


async def test_auth_isolation_abandon_and_config(dapi, pools, student):
    _, admin = pools
    sid = (await start(dapi, student)).json()["session_id"]
    other = await new_student(admin)
    # 没有令牌 401；别的学生读不到（与不存在同样 404）
    r = await dapi.get(f"/internal/v1/diagnosis/sessions/{sid}", headers={"x-student-ref": student, "authorization": "Bearer nope"})
    assert r.status_code == 401
    for who in (other, "not-a-uuid"):
        r = await dapi.get(f"/internal/v1/diagnosis/sessions/{sid}", headers={"x-student-ref": who})
        assert r.status_code == 404 and r.json()["code"] == "SESSION_NOT_FOUND"
    r = await dapi.get(f"/internal/v1/diagnosis/sessions/{uuid.uuid4()}", headers={"x-student-ref": student})
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
                           "LEARNING_DIAGNOSIS_TARGETS": '{"9": ["MATH.SYN.QUAD.APPLY"]}', "LEARNING_DIAGNOSIS_MAP_VERSION": "0.1.0"})
    assert s.knowledge is not None and s.diagnosis_targets == {9: ["MATH.SYN.QUAD.APPLY"]}
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


# ---------- 审查处理（安全审查 S1、S3、S5、S6，契约审查 B1、建议 4） ----------
async def answer_step(api, sid: str, student: str, step: dict, elapsed: int, *, correct: bool = True, key: str | None = None):
    body = {"student_ref": student, "seq": step["seq"], "answer": synthetic_answer(step["item_id"], correct), "elapsed_sec": elapsed,
            "idempotency_key": key or str(uuid.uuid4())}
    if step["kind"] != "adaptive":
        body["work"] = "合成过程：先列式，再求解。"
    return await api.post(f"/internal/v1/diagnosis/sessions/{sid}/answer", json=body)


async def step_elapsed(admin, sid: str, seq: int) -> tuple[int, int]:
    async with admin.acquire() as c:
        r = await c.fetchrow("""SELECT st.elapsed_sec, a.elapsed_sec AS attempt_sec FROM platform.diagnosis_step st
                                  JOIN platform.grade_result g ON g.id = st.grade_result_id
                                  JOIN platform.attempt a ON a.attempt_id = g.attempt_id
                                 WHERE st.session_id = $1::uuid AND st.seq = $2""", sid, seq)
    return r["elapsed_sec"], r["attempt_sec"]


async def test_elapsed_is_capped_by_wall_clock(dapi, pools, student):
    """用时按墙钟封顶（S1）：上报 7200 秒只记墙钟；上报 0 秒记 0；暂停前的用时续做后接着算；作答表里也是封顶值。"""
    _, admin = pools
    sid = (await start(dapi, student)).json()["session_id"]
    step = (await cur_view(dapi, sid, student)).json()["step"]
    assert (await answer_step(dapi, sid, student, step, 7200)).status_code == 200
    got, at = await step_elapsed(admin, sid, 1)
    assert got == at and got <= 5  # 刚出题就交：只记墙钟上的几秒
    step = (await cur_view(dapi, sid, student)).json()["step"]
    await backdate(admin, sid, step["seq"], 300)
    assert (await answer_step(dapi, sid, student, step, 0)).status_code == 200
    assert await step_elapsed(admin, sid, step["seq"]) == (0, 0)  # 上报 0 秒：记 0（不往上补）
    step = (await cur_view(dapi, sid, student)).json()["step"]
    await backdate(admin, sid, step["seq"], 100)
    assert (await answer_step(dapi, sid, student, step, 7200)).status_code == 200
    got, at = await step_elapsed(admin, sid, step["seq"])
    assert got == at and 100 <= got <= 105
    # 暂停：看题 50 秒后暂停、上报 3000 秒 → 记 50 左右；续做把 resumed_at 记为现在；续做后马上交 7200 → 50 左右 + 几秒
    step = (await cur_view(dapi, sid, student)).json()["step"]
    await backdate(admin, sid, step["seq"], 50)
    r = await dapi.post(f"/internal/v1/diagnosis/sessions/{sid}/pause", json={"student_ref": student, "seq": step["seq"], "step_elapsed_sec": 3000})
    assert r.status_code == 200
    v = (await cur_view(dapi, sid, student)).json()
    assert 50 <= v["step"]["step_elapsed_sec"] <= 55
    async with admin.acquire() as c:
        before = await c.fetchval("SELECT resumed_at FROM platform.diagnosis_session WHERE id = $1::uuid", sid)
    assert (await start(dapi, student)).json()["session_id"] == sid
    async with admin.acquire() as c:
        after = await c.fetchval("SELECT resumed_at FROM platform.diagnosis_session WHERE id = $1::uuid", sid)
    assert after > before
    assert (await answer_step(dapi, sid, student, step, 7200)).status_code == 200
    got, at = await step_elapsed(admin, sid, step["seq"])
    assert got == at and 50 <= got <= 60
    async with admin.acquire() as c:
        total = await c.fetchval("SELECT elapsed_sec FROM platform.diagnosis_session WHERE id = $1::uuid", sid)
    assert total <= 5 + 0 + 105 + 60


async def test_answer_key_conflict_and_retry(dapi, pools, student):
    """作答冲突（S5）：同键重放回原结果；键不同、内容相同回原结果；键不同、内容不同 409；判分请求键是 UUIDv4 形态。"""
    _, admin = pools
    sid = (await start(dapi, student)).json()["session_id"]
    step = (await cur_view(dapi, sid, student)).json()["step"]
    key = str(uuid.uuid4())
    a1 = await answer_step(dapi, sid, student, step, 20, key=key)
    assert a1.status_code == 200 and a1.headers["idempotent-replayed"] == "false"
    a2 = await answer_step(dapi, sid, student, step, 20, key=key)
    assert a2.status_code == 200 and a2.headers["idempotent-replayed"] == "true" and a2.json() == a1.json()
    a3 = await answer_step(dapi, sid, student, step, 45)  # 新键、同一答案（用时不同不算内容不同：用时取步骤上已定的值）
    assert a3.status_code == 200 and a3.headers["idempotent-replayed"] == "true"
    a4 = await answer_step(dapi, sid, student, step, 20, correct=False)
    assert a4.status_code == 409 and a4.json()["code"] == "ANSWER_CONFLICT"
    async with admin.acquire() as c:
        ak = await c.fetchval("SELECT answer_key::text FROM platform.diagnosis_step WHERE session_id = $1::uuid AND seq = 1", sid)
        gk = await c.fetch("""SELECT r.idempotency_key FROM platform.ingest_request r JOIN platform.attempt a ON a.request_idempotency_key = r.idempotency_key
                               WHERE a.student_id = $1::uuid""", student)
        grades = await c.fetchval("SELECT count(*) FROM platform.grade_result WHERE student_id = $1::uuid", student)
    assert ak == key and grades == 1
    from learning.diagnosis.service import UUID4_RE
    assert gk and all(UUID4_RE.fullmatch(r["idempotency_key"]) for r in gk)


async def test_probe_by_derived_key(dapi, pools, student):
    """探针幂等（S3）：BFF 用（会话、序号）派生的键写探针；并发提交的后到者按键找回同一条探针记到步骤上。"""
    _, admin = pools
    sid = (await start(dapi, student)).json()["session_id"]
    step = (await cur_view(dapi, sid, student)).json()["step"]
    key = str(uuid.uuid4())
    async with admin.acquire() as c:
        pid = await c.fetchval("""INSERT INTO insight.confidence_probe (student_id, subject, item_id, prediction, asked_at, idempotency_key)
                                  VALUES ($1::uuid, 'aya', $2, 'yes', now(), $3::uuid) RETURNING id::text""", student, step["item_id"], key)
    url = f"/internal/v1/diagnosis/sessions/{sid}/probe"
    r = await dapi.post(url, json={"student_ref": student, "seq": 1, "probe_key": key})
    assert r.status_code == 200 and r.headers["idempotent-replayed"] == "false"
    r = await dapi.post(url, json={"student_ref": student, "seq": 1, "probe_id": pid})
    assert r.status_code == 200 and r.headers["idempotent-replayed"] == "true"
    for bad in ({"student_ref": student, "seq": 1}, {"student_ref": student, "seq": 1, "probe_id": pid, "probe_key": key}):
        assert (await dapi.post(url, json=bad)).status_code == 400
    r = await dapi.post(url, json={"student_ref": student, "seq": 1, "probe_key": str(uuid.uuid4())})
    assert r.status_code == 422 and r.json()["code"] == "PROBE_MISMATCH"


async def test_student_ref_not_in_url_and_abandon_reports_answers(dapi, pools, student):
    sid = (await start(dapi, student)).json()["session_id"]
    r = await dapi.get(f"/internal/v1/diagnosis/sessions/{sid}", params={"student_ref": student})
    assert r.status_code == 400
    step = (await cur_view(dapi, sid, student)).json()["step"]
    assert (await answer_step(dapi, sid, student, step, 10)).status_code == 200
    r = await dapi.post(f"/internal/v1/diagnosis/sessions/{sid}/abandon", json={"student_ref": student})
    assert r.status_code == 200 and r.json() == {"status": "abandoned", "usage_id": r.json()["usage_id"], "item_count": 1,
                                                 "abandon_reason": None}
    v = (await cur_view(dapi, sid, student)).json()
    assert v["status"] == "abandoned" and v["process_tasks"] is False


async def test_synthetic_presenter_needs_test_database_and_candidate(monkeypatch):
    """合成题面除 LEARNING_ENV=test 外还要求库名 starbuddy_(dev|test)*（S6），并要求开候选契约（作答走 v1 写入路径）。"""
    from learning.app import _check_diagnosis_env

    class FakePool:
        def __init__(self, name):
            self.name = name

        async def fetchval(self, _sql):
            return self.name
    monkeypatch.setenv("STARBUDDY_ALLOW_CANDIDATE_CONTRACT", "1")
    for ok in ("starbuddy_test", "starbuddy_dev", "starbuddy_test_ci"):
        await _check_diagnosis_env(FakePool(ok))
    for bad in ("learning_test", "starbuddy", "starbuddy_pilot", "prod_starbuddy_test", "starbuddy_testx"):
        with pytest.raises(RuntimeError):
            await _check_diagnosis_env(FakePool(bad))
    monkeypatch.delenv("STARBUDDY_ALLOW_CANDIDATE_CONTRACT")
    with pytest.raises(RuntimeError):
        await _check_diagnosis_env(FakePool("starbuddy_test"))


def test_internal_openapi_matches_api_keys():
    """内部接口文档与 api.py 的请求键一致（契约审查建议 4）：每个 POST 的必填 / 可选键与 _keys(...) 的两个集合相同。"""
    import re as _re

    import yaml
    doc = yaml.safe_load((Path(__file__).resolve().parents[1] / "openapi/diagnosis-internal.v0.yaml").read_text(encoding="utf-8"))
    src = (Path(__file__).resolve().parents[1] / "learning/diagnosis/api.py").read_text(encoding="utf-8")
    routes = _re.findall(r'@app\.post\("([^"]+)"\)(.*?)(?=@app\.|\Z)', src, _re.S)
    assert len(routes) == 5
    for path, body in routes:
        m = _re.search(r"_keys\(b, (\{[^}]*\})(?:, (\{[^}]*\}))?\)", body)
        assert m, path
        req = set(eval(m.group(1)))  # noqa: S307 源码里的集合字面量
        opt = set(eval(m.group(2))) if m.group(2) else set()
        schema = doc["paths"][path]["post"]["requestBody"]["content"]["application/json"]["schema"]
        assert set(schema["required"]) == req, path
        assert set(schema["properties"]) == req | opt, path
        assert schema["additionalProperties"] is False, path
    # GET：学生 ID 在请求头
    params = doc["paths"]["/internal/v1/diagnosis/sessions/{session_id}"]["get"]["parameters"]
    assert {(p["name"], p["in"]) for p in params} == {("session_id", "path"), ("x-student-ref", "header")}
    assert 'request.headers.get("x-student-ref"' in src


async def test_internal_openapi_matches_responses(dapi, student):
    import yaml
    doc = yaml.safe_load((Path(__file__).resolve().parents[1] / "openapi/diagnosis-internal.v0.yaml").read_text(encoding="utf-8"))
    view_schema = doc["components"]["schemas"]["SessionView"]
    sid = (await start(dapi, student)).json()["session_id"]
    v = (await cur_view(dapi, sid, student)).json()
    assert set(v) == set(view_schema["properties"]) == set(view_schema["required"])
    step_schema = view_schema["properties"]["step"]["oneOf"][1]
    assert set(v["step"]) == set(step_schema["properties"]) == set(step_schema["required"])
    a = await answer_step(dapi, sid, student, v["step"], 5)
    ans = doc["paths"]["/internal/v1/diagnosis/sessions/{session_id}/answer"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert set(a.json()) == set(ans["required"]) == set(ans["properties"])
    r = await dapi.post(f"/internal/v1/diagnosis/sessions/{sid}/abandon", json={"student_ref": student})
    ab = doc["paths"]["/internal/v1/diagnosis/sessions/{session_id}/abandon"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert set(r.json()) == set(ab["required"]) == set(ab["properties"])


# ---------- 届别为真相源（specs/policy-decisions-v0.9.1 PR2）：选题年级 = platform.current_grade(届别, 时刻) ----------
# 时刻：开始时取服务时钟（测试注入，不依赖墙钟），记为会话建立时刻；之后每一步按建立时刻推算，跨 9 月 1 日续做选题不变。
# 合成学生建档于 2026-10-01（北京时间）、建档年级 8 → 推算届别 2028：2027-09-01 前是八年级，起是九年级（合成夹具只有九年级目标）。
SEP1_2027 = datetime(2027, 8, 31, 16, 0, tzinfo=timezone.utc)  # 北京时间 2027-09-01 00:00


async def cohort_student(admin, grade: int = 8, created_at: str = "2026-10-01 10:00+08") -> str:
    acct, guardian, household, sid = (str(uuid.uuid4()) for _ in range(4))
    async with admin.acquire() as c:
        await c.execute("INSERT INTO platform.account (id, kind) VALUES ($1::uuid, 'guardian'), ($2::uuid, 'student')", guardian, acct)
        await c.execute("INSERT INTO platform.household (id, created_by_account) VALUES ($1::uuid, $2::uuid)", household, guardian)
        await c.execute("""INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, created_at)
                           VALUES ($1::uuid, $2::uuid, $3::uuid, $4, 'nanjing', 'suke-2024', $5::text::timestamptz)""",
                        sid, acct, household, grade, created_at)
        assert await c.fetchval("SELECT exam_cohort FROM platform.student_profile WHERE id = $1::uuid", sid) == 2028 + (8 - grade)
    return sid


async def test_grade_from_cohort_with_injected_clock(pools, aya_v1):
    app_pool, admin = pools
    now = {"t": SEP1_2027 - timedelta(seconds=1)}
    s = make_settings(adapters={"aya": AdapterTarget(aya_v1, AYA_CREDENTIAL)}, diagnosis_presenter="synthetic")
    app = create_app(s, pool=app_pool, diagnosis_clock=lambda: now["t"])
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test", timeout=60) as c:
            c.headers["authorization"] = f"Bearer {CREDENTIAL}"
            stu = await cohort_student(admin)
            # 2027-08-31 23:59:59（北京时间）还是八年级：夹具没有八年级目标；存下的建档年级（8）不再被读
            r = await start(c, stu)
            assert r.status_code == 422 and r.json()["code"] == "NO_TARGETS"
            # 跨过 9 月 1 日：九年级，可以开始；会话建立时刻 = 注入的时钟
            now["t"] = SEP1_2027
            r = await start(c, stu)
            assert r.status_code == 201, r.text
            sid = r.json()["session_id"]
            async with admin.acquire() as a:
                assert await a.fetchval("SELECT created_at FROM platform.diagnosis_session WHERE id = $1::uuid", sid) == SEP1_2027
                # 建立时用于选题的年级记在会话上（迁移 0049），续做时据此发现中途改届别
                assert await a.fetchval("SELECT start_grade FROM platform.diagnosis_session WHERE id = $1::uuid", sid) == 9
            first = (await cur_view(c, sid, stu)).json()["step"]
            # 一年后续做（按当前时刻已毕业）：年级按会话建立时刻推算，仍是同一个会话、同一道题
            now["t"] = datetime(2028, 9, 1, 2, 0, tzinfo=timezone.utc)
            r = await start(c, stu)
            assert r.status_code == 200 and r.json()["session_id"] == sid and r.json()["created"] is False
            assert (await cur_view(c, sid, stu)).json()["step"] == first
            # 新建档的学生按当前时刻已毕业（年级不在 7–9）：与“这个年级暂无诊断”同一个 422；没有档案仍是 404
            gone = await cohort_student(admin, grade=9, created_at="2026-10-01 10:00+08")
            r = await start(c, gone)
            assert r.status_code == 422 and r.json()["code"] == "NO_TARGETS"
            r = await start(c, str(uuid.uuid4()))
            assert r.status_code == 404 and r.json()["code"] == "STUDENT_NOT_FOUND"
            # 会话中途届别被更正、建立时刻的年级不在 7–9（迁移执行者直接改届别与来源，模拟客服更正）：按“与选题规则不符”409
            async with admin.acquire() as a:
                await a.execute("UPDATE platform.student_profile SET exam_cohort = 2026, exam_cohort_source = 'staff_correction' WHERE id = $1::uuid", stu)
            r = await start(c, stu)
            assert r.status_code == 409 and r.json()["code"] == "SESSION_INCONSISTENT"


async def test_cohort_corrected_mid_session_grade_still_in_range(pools, aya_v1):
    """会话中途届别被更正、建立时刻的年级变了但仍在 7–9（九年级 → 八年级）：续做按会话建立时刻、新届别重算的年级与建立时记下的
    选题年级（start_grade，迁移 0049）不同 → 409 SESSION_INCONSISTENT（specs/policy-decisions-v0.9.1 PR3 #12）。
    不再静默把会话标为结束：会话仍是进行中，已出未答的那一步留在原处，没有多记步骤。届别改回来后可以照常续做。
    0049 之前建立的会话（start_grade 为空）按 PR2 的行为处理（见 test_cohort_corrected_mid_session_legacy_session）。"""
    app_pool, admin = pools
    s = make_settings(adapters={"aya": AdapterTarget(aya_v1, AYA_CREDENTIAL)}, diagnosis_presenter="synthetic")
    app = create_app(s, pool=app_pool, diagnosis_clock=lambda: SEP1_2027)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test", timeout=60) as c:
            c.headers["authorization"] = f"Bearer {CREDENTIAL}"
            stu = await cohort_student(admin)          # 届别 2028：2027-09-01 起九年级
            r = await start(c, stu)
            assert r.status_code == 201, r.text
            sid = r.json()["session_id"]
            assert (await cur_view(c, sid, stu)).json()["step"]["seq"] == 1  # 已出第一题
            # 客服把届别改晚一届（迁移执行者直接改届别与来源）：建立时刻的年级变成八年级，仍在 7–9
            async with admin.acquire() as a:
                await a.execute("UPDATE platform.student_profile SET exam_cohort = 2029, exam_cohort_source = 'staff_correction' WHERE id = $1::uuid", stu)
                assert await a.fetchval("SELECT platform.current_grade(2029::smallint, $1::timestamptz)", SEP1_2027) == 8
            # 续做与读当前一步：建立时刻的年级与记下的不同 → 409，会话不动
            r = await start(c, stu)
            assert r.status_code == 409 and r.json()["code"] == "SESSION_INCONSISTENT", r.text
            # 问题体带这个会话的 ID 与原因（前端据此“重新开始”，不卡死）
            assert r.json()["session_id"] == sid and r.json()["reason"] == "grade_changed", r.text
            async with admin.acquire() as a:
                assert await a.fetchval("SELECT status FROM platform.diagnosis_session WHERE id = $1::uuid", sid) == "active"
                assert await a.fetchval("SELECT count(*) FROM platform.diagnosis_step WHERE session_id = $1::uuid", sid) == 1  # 没有多记步骤
                assert await a.fetchval("SELECT status FROM platform.diagnosis_step WHERE session_id = $1::uuid", sid) == "presented"
            # 届别改回来（建立时刻的年级又是九年级）：同一会话照常续做
            async with admin.acquire() as a:
                await a.execute("UPDATE platform.student_profile SET exam_cohort = 2028, exam_cohort_source = 'guardian_report' WHERE id = $1::uuid", stu)
            r = await start(c, stu)
            assert r.status_code == 200 and r.json()["session_id"] == sid and r.json()["status"] == "active", r.text
            # 再改一次，这回“重新开始”：放弃时记下原因 grade_changed（BFF 据此退回额度）；重放返回同一个原因；之后能开新会话
            async with admin.acquire() as a:
                await a.execute("UPDATE platform.student_profile SET exam_cohort = 2029, exam_cohort_source = 'staff_correction' WHERE id = $1::uuid", stu)
            r = await c.post(f"/internal/v1/diagnosis/sessions/{sid}/abandon", json={"student_ref": stu})
            assert r.status_code == 200 and r.json()["abandon_reason"] == "grade_changed" and r.headers["idempotent-replayed"] == "false", r.text
            r = await c.post(f"/internal/v1/diagnosis/sessions/{sid}/abandon", json={"student_ref": stu})
            assert r.status_code == 200 and r.json()["abandon_reason"] == "grade_changed" and r.headers["idempotent-replayed"] == "true", r.text
            async with admin.acquire() as a:
                assert await a.fetchval("SELECT abandon_reason FROM platform.diagnosis_session WHERE id = $1::uuid", sid) == "grade_changed"
            # 读这个会话的视图也带放弃原因（BFF 丢了放弃结果时，读到已放弃 + grade_changed 也会去退回额度，审查 S5）
            r = await c.get(f"/internal/v1/diagnosis/sessions/{sid}", headers={"x-student-ref": stu})
            assert r.status_code == 200 and r.json()["status"] == "abandoned" and r.json()["abandon_reason"] == "grade_changed", r.text
            r = await start(c, stu)  # 新届别下现在是八年级，合成夹具没有八年级目标：与“这个年级暂无诊断”同一个 422（不是卡在旧会话上）
            assert r.status_code == 422 and r.json()["code"] == "NO_TARGETS", r.text


async def test_ordinary_abandon_has_no_reason(pools, aya_v1):
    """普通放弃（届别没变）：abandon_reason 为空，BFF 按原规则（答过题不退）处理。"""
    app_pool, admin = pools
    s = make_settings(adapters={"aya": AdapterTarget(aya_v1, AYA_CREDENTIAL)}, diagnosis_presenter="synthetic")
    app = create_app(s, pool=app_pool, diagnosis_clock=lambda: SEP1_2027)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test", timeout=60) as c:
            c.headers["authorization"] = f"Bearer {CREDENTIAL}"
            stu = await cohort_student(admin)
            sid = (await start(c, stu)).json()["session_id"]
            r = await c.post(f"/internal/v1/diagnosis/sessions/{sid}/abandon", json={"student_ref": stu})
            assert r.status_code == 200 and r.json()["abandon_reason"] is None, r.text
            async with admin.acquire() as a:
                assert await a.fetchval("SELECT abandon_reason FROM platform.diagnosis_session WHERE id = $1::uuid", sid) is None


async def test_cohort_corrected_mid_session_legacy_session(pools, aya_v1):
    """0049 之前建立的会话没有记下选题年级（start_grade 为空）：中途改届别仍按 PR2 的行为——年级按建立时刻、新届别重算，
    选题规则用新年级复算已记下的步骤；本例新年级在合成夹具里没有目标，复算判定已结束，会话标为 finished（已出未答的一步留在原处）。"""
    app_pool, admin = pools
    s = make_settings(adapters={"aya": AdapterTarget(aya_v1, AYA_CREDENTIAL)}, diagnosis_presenter="synthetic")
    app = create_app(s, pool=app_pool, diagnosis_clock=lambda: SEP1_2027)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test", timeout=60) as c:
            c.headers["authorization"] = f"Bearer {CREDENTIAL}"
            stu = await cohort_student(admin)
            r = await start(c, stu)
            assert r.status_code == 201, r.text
            sid = r.json()["session_id"]
            assert (await cur_view(c, sid, stu)).json()["step"]["seq"] == 1
            async with admin.acquire() as a:
                # 模拟 0049 之前建立的会话（迁移执行者清空这一列），再把届别改晚一届
                await a.execute("UPDATE platform.diagnosis_session SET start_grade = NULL WHERE id = $1::uuid", sid)
                await a.execute("UPDATE platform.student_profile SET exam_cohort = 2029, exam_cohort_source = 'staff_correction' WHERE id = $1::uuid", stu)
            r = await start(c, stu)
            assert r.status_code == 200 and r.json()["session_id"] == sid and r.json()["status"] == "finished", r.text
            async with admin.acquire() as a:
                assert await a.fetchval("SELECT count(*) FROM platform.diagnosis_step WHERE session_id = $1::uuid", sid) == 1
