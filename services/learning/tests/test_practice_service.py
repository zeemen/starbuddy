"""错题再练 PR 2 集成测试（specs/practice-loop-stage1/spec.md 第 1–4 节、验收标准）：学习服务读取层与三个内部接口，一次性库。

本模块独占一个一次性 Postgres（bootstrap 角色与全部迁移），合成地图版本、合成题目族与合成题（SYNP-*），合成学生每个测试一个。
知识服务与适配器是按契约形状应答的假服务（httpx.MockTransport，按主机分流）：知识服务按种子里的打标回 item.similar / item.search，
适配器的 grade 沿用 conftest 的假判分（"42" 为对），generate_variation 从 item_family 里选第一道没被排除的题（可改成越界、空结果）。
掌握重算用合成配置（只在内存里“签字”，同 test_mastery_db）。服务代码只以 starbuddy_learning 身份读写；测试夹具以超级用户写种子。
覆盖：错 → 选 → 对 → 闭环、再错 → 下一轮、做满轮次、无候选、同族兜底、适配器越界、票据伪造 / 过期 / 跨学生 / 跨学科、
幂等重放、重算失败不影响作答、hint_followup 由 KGE-04 关闭、掌握不受本任务影响、鉴权只认 BFF 令牌、服务目录 OpenAPI 一致。
全部合成数据（规则 8）。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
import httpx
import pytest

from conftest import ADAPTER_CREDENTIAL, CREDENTIAL, FIXTURE_SQL, _answer_in_version, fake_grade, grade_body, settings, to_v1_body
from learning.app import create_app
from learning.config import AdapterTarget
from learning.mastery import recompute as R
from learning.practice import ticket as T
from pgharness import LOGIN_ROLE, EphemeralPG
from test_mastery_db import SIGNED

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

PV = "5b8e1c52-0000-4000-8000-0000000c0001"
CVP = "e0000000-0000-4000-8000-0000000c00e1"
CV = {"region": "nanjing", "exam_year": 2027, "grade": 9, "textbook_version": "synp-2024", "subject": "aya", "content_version": "0.1.0"}
N1, N2, N3 = "MATH.SYP.T1.N1", "MATH.SYP.T1.N2", "MATH.SYP.T1.N3"
SECRET = "synthetic-practice-ticket-secret-0000000001"
KNOWLEDGE = AdapterTarget("http://knowledge.invalid", "synthetic-learning-to-knowledge-credential-1")
ORCH, INSIGHT = "synthetic-orchestrator-to-learning-cred-0001", "synthetic-insight-to-learning-credential-01"
OPENAPI = Path(__file__).resolve().parents[1] / "openapi/practice-internal.v0.yaml"
FORBIDDEN = ("p_mastery", "mastery", "confidence", "evidence_id", "error_categor", "subject_category", "粗心", "薄弱", "answer_key")

# 题目：(题号, 主节点, 层次, 题目族, 准入, 版权)
ITEMS = [
    ("SYNP-0001", N1, "apply", "fam-a", "admitted", "original"),
    ("SYNP-0002", N1, "apply", "fam-a", "admitted", "original"),
    ("SYNP-0003", N1, "apply", "fam-b", "admitted", "original"),
    ("SYNP-0004", N1, "apply", "fam-c", "admitted", "original"),
    ("SYNP-0005", N1, "apply", None, "admitted", "original"),
    ("SYNP-0011", N1, "apply", "fam-d", "admitted", "original"),
    ("SYNP-0012", N1, "understand", "fam-a", "admitted", "original"),     # 同节点不同层次：不出
    ("SYNP-0013", N1, "apply", "fam-i", "admitted", "original"),          # 主观题（合成题面标 model_rubric）：不出（审查 S7）
    ("SYNP-0006", N2, "apply", "fam-e", "admitted", "original"),
    ("SYNP-0007", N2, "apply", "fam-e", "admitted", "original"),          # N2 只有同族候选
    ("SYNP-0008", N3, "apply", "fam-f", "admitted", "original"),
    ("SYNP-0009", N3, "apply", "fam-g", "pending_review", "original"),    # 未准入
    ("SYNP-0010", N3, "apply", "fam-h", "admitted", "restricted"),        # 版权受限
]


def _seed() -> str:
    fams = sorted({f for _, _, _, f, _, _ in ITEMS if f})
    fam_id = {f: str(uuid.uuid5(uuid.UUID(PV), f)) for f in fams}
    node_of = {f: next(n for _, n, _, ff, _, _ in ITEMS if ff == f) for f in fams}
    sql = [f"""
SET session_replication_role = replica;  -- 一次性测试库：直接写合成地图、题目族与打标（发布与准入守卫只允许各自的闸门）
INSERT INTO kge.map_version (id, subject, standard_year, version, status, published_at)
  VALUES ('{PV}', 'aya', 2022, '1.0.0', 'published', '2026-09-01T00:00:00Z');
INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, cognitive_levels) VALUES
  ('{PV}', 'MATH.SYP', 'domain', NULL, NULL, '（合成）领域', '{{}}'),
  ('{PV}', 'MATH.SYP.T1', 'topic', 'MATH.SYP', NULL, '（合成）主题', '{{}}'),
  ('{PV}', '{N1}', 'node', 'MATH.SYP.T1', 'kp', '（合成）节点一', '{{understand,apply}}'),
  ('{PV}', '{N2}', 'node', 'MATH.SYP.T1', 'kp', '（合成）节点二', '{{apply}}'),
  ('{PV}', '{N3}', 'node', 'MATH.SYP.T1', 'kp', '（合成）节点三', '{{apply}}');
INSERT INTO platform.curriculum_version (id, region, exam_year, grade, textbook_version, subject, content_version, map_version_id)
  VALUES ('{CVP}', 'nanjing', 2027, 9, 'synp-2024', 'aya', '0.1.0', '{PV}');
"""]
    for f in fams:
        sql.append(f"INSERT INTO kge.item_family (id, map_version_id, main_node_id, code) VALUES ('{fam_id[f]}', '{PV}', '{node_of[f]}', '{f}');")
    for iid, node, level, fam, adm, lic in ITEMS:
        ok = adm == "admitted"
        sql.append(f"""INSERT INTO platform.item (subject, item_id, curriculum_version_id, admission, admitted_at, source_ref, source_type, license_status)
  VALUES ('aya', '{iid}', '{CVP}', '{adm}', {"now()" if ok else "NULL"}, {"'SYN-SRC-PRACTICE'" if ok else "NULL"},
          '{"self_authored" if ok else "unknown"}', '{"cleared" if ok else "unverified"}');""")
        sql.append(f"""INSERT INTO kge.item_tag (map_version_id, subject, item_id, main_node_id, node_ids, cognitive_level, family_id, source_kind,
                                 license_state, tag_review_status)
  VALUES ('{PV}', 'aya', '{iid}', '{node}', '{{}}', '{level}', {f"'{fam_id[fam]}'" if fam else "NULL"}, 'original', '{lic}', 'approved');""")
    sql.append("SET session_replication_role = origin;")
    return "\n".join(sql)


@pytest.fixture(scope="module")
def ppg():
    try:
        db = EphemeralPG()
    except FileNotFoundError as e:
        if os.environ.get("LEARNING_TEST_STRICT") == "1":
            raise
        pytest.skip(str(e))
    db.start()
    db.psql(FIXTURE_SQL)
    db.psql(_seed())
    yield db
    db.stop()


@pytest.fixture(scope="module")
async def ppools(ppg):
    app_pool = await asyncpg.create_pool(**ppg.dsn(LOGIN_ROLE), min_size=1, max_size=30)
    admin = await asyncpg.create_pool(**ppg.dsn("postgres"), min_size=1, max_size=4)
    yield app_pool, admin
    await app_pool.close()
    await admin.close()


@pytest.fixture(scope="module")
async def plock(ppg):
    """再练作答串行锁的独立小连接池（审查 B2；app 注入业务池时必须一并注入）。"""
    pool = await asyncpg.create_pool(**ppg.dsn(LOGIN_ROLE), min_size=0, max_size=8)
    yield pool
    await pool.close()


# 取到 app 里的 PracticeService（看计数用）：包一层 learning.app._practice，lifespan 运行时调用的是模块全局
SVCS: list = []


@pytest.fixture(autouse=True)
def capture_practice_service(monkeypatch):
    import learning.app as app_module
    orig = app_module._practice

    def wrapped(*a, **kw):
        svc = orig(*a, **kw)
        SVCS.append(svc)
        return svc
    monkeypatch.setattr(app_module, "_practice", wrapped)
    yield
    SVCS.clear()


@pytest.fixture(scope="module", autouse=True)
def candidate_contract():
    mp = pytest.MonkeyPatch()
    mp.setenv("STARBUDDY_ALLOW_CANDIDATE_CONTRACT", "1")   # 签字前只在测试里开（generate_variation 与 v1 写入路径）
    yield
    mp.undo()


@pytest.fixture
async def student(ppools) -> str:
    _, admin = ppools
    return await new_student(admin)


async def new_student(admin) -> str:
    acct, guardian, household, sid = (str(uuid.uuid4()) for _ in range(4))
    async with admin.acquire() as c:
        await c.execute("INSERT INTO platform.account (id, kind) VALUES ($1::uuid, 'guardian'), ($2::uuid, 'student')", guardian, acct)
        await c.execute("INSERT INTO platform.household (id, created_by_account) VALUES ($1::uuid, $2::uuid)", household, guardian)
        await c.execute(
            """INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month)
               VALUES ($1::uuid, $2::uuid, $3::uuid, 9, 'nanjing', 'suke-2024', '2012-03-01')""", sid, acct, household)
    return sid


# ---------- 假知识服务与假适配器 ----------
def _view(row) -> dict:
    iid, node, level, fam, _, lic = row
    v = {"item_ref": f"aya:{iid}", "main_node_id": node, "cognitive_level": level, "license_state": lic}
    if fam:
        v["family_code"] = fam
    return v


def _envelope(result: dict, model: str) -> dict:
    return {"contract_version": "0.9.0", "result": result, "evidence_ids": [], "confidence": 1,
            "source_version": "items:nanjing-2027-9@0.1.0", "model_version": model, "human_review_required": False}


class Fakes:
    def __init__(self) -> None:
        self.calls: dict[str, int] = {}
        self.variation_mode = "first"      # first / outside / empty / not_admitted / status501
        self.variation_requests: list[dict] = []
        self.grade_mode = "ok"             # ok / bad（返回非 JSON：判分失败，幂等表记 failed）
        self.grade_delay = 0.0             # 判分延时（秒）：并发用例用

    def _count(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1

    def knowledge(self, path: str, body: dict) -> httpx.Response:
        assert "student_ref" not in json.dumps(body)       # 公共层请求不带学生标识（规则 13）
        if path == "/v0/item.similar":
            self._count("item.similar")
            src = next(r for r in ITEMS if f"aya:{r[0]}" == body["item_ref"])
            rows = [r for r in ITEMS if r[0] != src[0] and ((src[3] and r[3] == src[3]) or r[1] == src[1])]
            rows.sort(key=lambda r: (not (src[3] and r[3] == src[3]), r[0]))
            items = [{"item": _view(r), "relation": "same_family" if src[3] and r[3] == src[3] else "similar"} for r in rows[:body["limit"]]]
            return httpx.Response(200, json=_envelope({"items": items}, "algo:kge-item-similar@0.1.0"))
        if path == "/v0/item.search":
            self._count("item.search")
            rows = [r for r in ITEMS if r[1] == body["node_id"] and r[2] == body.get("cognitive_level", r[2])]
            return httpx.Response(200, json=_envelope({"items": [_view(r) for r in rows[:body["limit"]]]}, "algo:kge-item-search@0.1.0"))
        return httpx.Response(404, json={})

    def variation(self, body: dict) -> httpx.Response:
        self._count("generate_variation")
        self.variation_requests.append(body)
        if self.variation_mode == "status501":
            return httpx.Response(501, json={"code": "NOT_IMPLEMENTED", "retryable": False})
        if self.variation_mode == "busy":
            return httpx.Response(503, json={"code": "BUSY", "retryable": True})
        excluded = set(body.get("exclude_item_refs", []))
        refs = [f["item_ref"] for f in body["item_family"] if f["item_ref"] not in excluded]
        if self.variation_mode == "outside":
            refs = ["aya:SYNP-0002"]                       # 不在交给它的那一档里（与原题同族）
        if self.variation_mode == "not_admitted":
            refs = ["aya:SYNP-0009"]
        cands = [{"item_ref": r, "admission": "admitted", "content_version": dict(CV), "match": {"same_skill_node": True,
                                                                                                "same_error_category": False}}
                 for r in refs[:body["count"]]]
        result: dict = {"candidates": [] if self.variation_mode == "empty" else cands}
        if not result["candidates"]:
            result["empty_reason"] = "no_admitted_candidate"
        return httpx.Response(200, json={"contract_version": "1.0.0", "result": result, "evidence_ids": [], "confidence": 1.0,
                                         "source_version": "algo:aya-variant-select@0.1.0;items:syn-practice@0.0.1",
                                         "model_version": "algo:aya-variant-select@0.1.0", "human_review_required": False,
                                         "semantics": "formal"})

    async def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.host == "knowledge.invalid":
            assert request.headers["authorization"] == f"Bearer {KNOWLEDGE.credential}"
            return self.knowledge(request.url.path, body)
        assert request.headers["authorization"] == f"Bearer {ADAPTER_CREDENTIAL}"
        method = request.url.path.rsplit("/", 1)[-1]
        if method == "generate_variation":
            return self.variation(body)
        assert method == "grade"
        self._count("grade")
        if self.grade_delay:
            await asyncio.sleep(self.grade_delay)
        if self.grade_mode == "bad":
            return httpx.Response(200, content=b"not json", headers={"content-type": "application/json"})
        out = _answer_in_version(body, fake_grade(body, "admitted"))
        if body["attempt"]["response"].get("text") == "review":
            # 验证器边界情况：判分待人工复核（verdict 照常给出，human_review_required = true）
            out["human_review_required"] = True
            out["review_reasons"] = ["verifier_boundary_case"]
        return httpx.Response(200, content=json.dumps(out).encode(), headers={"content-type": "application/json"})


OFFSET = [timedelta(0)]


def clock() -> datetime:
    return datetime.now(timezone.utc) + OFFSET[0]


@pytest.fixture(autouse=True)
def reset_clock():
    OFFSET[0] = timedelta(0)
    yield
    OFFSET[0] = timedelta(0)


def practice_settings(**over):
    base = dict(adapters={"aya": AdapterTarget("http://aya-adapter.invalid", ADAPTER_CREDENTIAL)}, knowledge=KNOWLEDGE,
                practice_config="synthetic", practice_presenter="synthetic", practice_ticket_secret=SECRET,
                orchestrator_client_credential=ORCH, insight_client_credential=INSIGHT)
    base.update(over)
    return settings(**base)


def engine(app_pool, **kw) -> R.MasteryEngine:
    return R.MasteryEngine(app_pool, {"aya": SIGNED["aya"]}, **kw)


class Api:
    def __init__(self, client: httpx.AsyncClient, fakes: Fakes, app) -> None:
        self.c, self.fakes, self.app = client, fakes, app

    @property
    def svc(self):
        return SVCS[-1]

    async def queue(self, student, subject="aya", **kw):
        return await self.c.post("/internal/v1/practice/queue", json={"student_ref": student, "subject": subject}, **kw)

    async def entries(self, student):
        r = await self.queue(student)
        assert r.status_code == 200, r.text
        return r.json()["entries"]

    async def next(self, student, entry_id, subject="aya"):
        return await self.c.post("/internal/v1/practice/next", json={"student_ref": student, "subject": subject, "entry_id": entry_id})

    async def answer(self, student, ticket, text="42", *, key=None, usage=None, subject="aya", extra=None, answer=None):
        body = {"student_ref": student, "subject": subject, "ticket": ticket, "answer": answer or {"text": text},
                "idempotency_key": key or str(uuid.uuid4()), "usage_id": usage or str(uuid.uuid4())}
        body.update(extra or {})
        return await self.c.post("/internal/v1/practice/answer", json=body)

    async def grade(self, student, item, text, hint=0, channel="keypad"):
        """平台作答（不经再练）：learning-internal v1 的 grade（缺省键盘通道；拍照通道记 B 级）。"""
        b = to_v1_body(grade_body(student, item=item, text=text))
        b["context"]["content_version"] = dict(CV)
        b["attempt"]["hint_level_used"] = hint
        b["attempt"]["occurred_at"] = clock().isoformat()
        if channel == "photo":
            b["evidence_level"] = "B"
        r = await self.c.post("/internal/v1/grade", json={"request": b, "ingest": {"channel": channel}})
        assert r.status_code == 200, r.text
        return r.json()


async def make_api(app_pool, lock_pool, *, mastery=None, **over):
    fakes = Fakes()
    app = create_app(practice_settings(**over), pool=app_pool, transport=httpx.MockTransport(fakes.handler), mastery=mastery,
                     practice_clock=clock, practice_lock_pool=lock_pool)
    return app, fakes


@pytest.fixture
async def api(ppools, plock):
    app_pool, _ = ppools
    app, fakes = await make_api(app_pool, plock, mastery=engine(app_pool))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as c:
            c.headers["authorization"] = f"Bearer {CREDENTIAL}"
            yield Api(c, fakes, app)


TABLES = ("evidence_event", "attempt", "grade_result", "error_attribution", "mastery_estimate", "retest_task", "ingest_request")


async def counts(admin, student) -> dict:
    async with admin.acquire() as c:
        return {t: await c.fetchval(f"SELECT count(*) FROM platform.{t} WHERE student_id = $1::uuid", student) for t in TABLES}


def no_forbidden(payload) -> None:
    text = json.dumps(payload, ensure_ascii=False)
    for word in FORBIDDEN:
        assert word not in text, word


async def due_entry(api: Api, student: str, node: str = N1) -> dict:
    es = [e for e in await api.entries(student) if e["node_id"] == node]
    assert len(es) == 1, es
    return es[0]


# ---------- 错 → 选 → 对 → 闭环 ----------
async def test_wrong_select_correct_closes(api, ppools, student):
    _, admin = ppools
    s = await api.grade(student, "SYNP-0001", "41")
    assert s["verdict"] == "incorrect"
    e = await due_entry(api, student)
    assert (e["status"], e["round"], e["source"], e["source_item_ref"], e["same_family_only"], e["retest_due"]) == \
        ("due", 1, "wrong", "aya:SYNP-0001", False, False)
    no_forbidden(e)
    before = await counts(admin, student)
    r = await api.next(student, e["entry_id"])
    assert r.status_code == 200, r.text
    n = r.json()
    # 不同族优先：原题所在的 fam-a（SYNP-0002）不出；不同层次的 SYNP-0012 不出；按题号第一道是 SYNP-0003（fam-b）
    assert n["status"] == "ready" and n["item_ref"] == "aya:SYNP-0003" and n["same_family_only"] is False
    assert set(n["item"]) == {"format", "stem"} and n["round"] == 1
    no_forbidden(n)
    req = api.fakes.variation_requests[-1]
    assert {f["item_ref"] for f in req["item_family"]} == {"aya:SYNP-0003", "aya:SYNP-0004", "aya:SYNP-0005", "aya:SYNP-0011"}
    assert req["source_item_ref"] == "aya:SYNP-0001" and "aya:SYNP-0001" in req["exclude_item_refs"]
    assert req["subject_error_category"] == "yunsuan_cuowu" and "usage_id" not in req["context"]
    assert await counts(admin, student) == before                       # queue、next 不写任何表
    a = await api.answer(student, n["ticket"], "42")
    assert a.status_code == 200, a.text
    assert a.headers["idempotent-replayed"] == "false"
    out = a.json()
    assert out == {"verdict": "correct", "entry": {"entry_id": e["entry_id"], "status": "closed", "round": 1, "next_round_at": None}}
    no_forbidden(out)
    after = await counts(admin, student)
    assert after["attempt"] == before["attempt"] + 1 and after["grade_result"] == before["grade_result"] + 1
    async with admin.acquire() as c:
        row = await c.fetchrow("""SELECT a.channel, a.hint_level_used, a.evidence_kind, a.evidence_level, a.item_id::text
                                     FROM platform.attempt a WHERE a.student_id = $1::uuid ORDER BY a.recorded_at DESC LIMIT 1""", student)
        cur = await c.fetchval("""SELECT count(*) FROM platform.mastery_estimate WHERE student_id = $1::uuid AND node_id = $2
                                    AND cognitive_level = 'apply' AND is_current""", student, N1)
    # 服务端写入：键盘通道、练习用途（→ independent）、无提示、A 级
    assert tuple(row) == ("keypad", 0, "independent", "A", "SYNP-0003")
    assert cur == 1                                                     # 作答触发了同步重算（掌握由 KGE-04 写）
    assert (await due_entry(api, student))["status"] == "closed"
    # 闭环后再 next：409
    assert (await api.next(student, e["entry_id"])).json()["code"] == "PRACTICE_NOT_DUE"


# ---------- 再错 → 下一轮；做满轮次 ----------
async def test_wrong_rounds_wait_then_exhaust(api, ppools, student):
    await api.grade(student, "SYNP-0001", "41")
    e = await due_entry(api, student)
    n = (await api.next(student, e["entry_id"])).json()
    a = (await api.answer(student, n["ticket"], "41")).json()
    assert a["verdict"] == "incorrect" and a["entry"]["status"] == "waiting" and a["entry"]["round"] == 2
    wait_until = datetime.fromisoformat(a["entry"]["next_round_at"])
    assert timedelta(hours=47) < wait_until - clock() <= timedelta(hours=48)
    assert len(await api.entries(student)) == 1                           # 再练做错不另开条目
    r = await api.next(student, e["entry_id"])
    assert r.status_code == 409 and r.json()["code"] == "PRACTICE_NOT_DUE"
    OFFSET[0] = timedelta(hours=49)
    e2 = await due_entry(api, student)
    assert (e2["status"], e2["round"]) == ("due", 2)
    n2 = (await api.next(student, e["entry_id"])).json()
    # 做过的题（SYNP-0001、SYNP-0003）与它们的族都排除：下一道不同族是 SYNP-0004（fam-c）
    assert n2["item_ref"] == "aya:SYNP-0004"
    # 上一轮的票据：过期（且轮次已变）
    old = await api.answer(student, n["ticket"], "42")
    assert old.status_code == 410 and old.json()["code"] == "TICKET_EXPIRED"
    assert (await api.answer(student, n2["ticket"], "41")).json()["entry"]["status"] == "waiting"
    OFFSET[0] = timedelta(hours=49 + 121)
    n3 = (await api.next(student, e["entry_id"])).json()
    assert n3["item_ref"] == "aya:SYNP-0005"                               # 没有族的题按单题自成一族
    a3 = (await api.answer(student, n3["ticket"], "41")).json()
    assert a3["entry"] == {"entry_id": e["entry_id"], "status": "exhausted", "round": 3, "next_round_at": None}
    assert (await due_entry(api, student))["status"] == "exhausted"
    assert (await api.next(student, e["entry_id"])).json()["code"] == "PRACTICE_NOT_DUE"


# ---------- 无候选、同族兜底、适配器越界 ----------
async def test_no_candidate_returns_unavailable_without_generating(api, student):
    await api.grade(student, "SYNP-0008", "41")
    e = await due_entry(api, student, N3)
    # N3 的其他题：一道未准入、一道版权受限
    assert (e["status"], e["empty_reason"]) == ("unavailable", "no_admitted_candidate")
    r = await api.next(student, e["entry_id"])
    assert r.status_code == 200 and r.json() == {"status": "unavailable", "empty_reason": "no_admitted_candidate"}
    assert api.fakes.calls.get("generate_variation", 0) == 0               # 平台过滤后没有候选：不调适配器，更不生成新题
    assert api.fakes.calls["item.search"] >= 1                             # similar 不够时用 search 补足


async def test_same_family_fallback(api, student):
    await api.grade(student, "SYNP-0006", "41")
    e = await due_entry(api, student, N2)
    assert e["status"] == "due" and e["same_family_only"] is True
    n = (await api.next(student, e["entry_id"])).json()
    assert n["item_ref"] == "aya:SYNP-0007" and n["same_family_only"] is True
    assert (await api.answer(student, n["ticket"], "42")).json()["entry"]["status"] == "closed"   # 同族做对也闭环（不计入掌握由 KGE-04 判）


async def test_photo_wrong_does_not_enter(api, ppools, student):
    """拍照上传的错题不进再练（上传深度分析是另一项计量资源，KGE-05）；同单元键盘错题照常进。"""
    _, admin = ppools
    s = await api.grade(student, "SYNP-0001", "41", channel="photo")
    assert s["verdict"] == "incorrect"
    async with admin.acquire() as c:
        assert await c.fetchval("SELECT channel FROM platform.attempt WHERE student_id = $1::uuid", student) == "photo"
    assert await api.entries(student) == []
    await api.grade(student, "SYNP-0002", "41")
    assert (await due_entry(api, student))["source_item_ref"] == "aya:SYNP-0002"


async def test_done_items_are_excluded(api, student):
    """N2 只有一道同族候选：再练做错之后，下一轮它已在排除窗口内，这一轮没有可练的题（不重推做过的题）。"""
    await api.grade(student, "SYNP-0006", "41")
    e = await due_entry(api, student, N2)
    n = (await api.next(student, e["entry_id"])).json()
    assert n["item_ref"] == "aya:SYNP-0007"
    assert (await api.answer(student, n["ticket"], "41")).json()["entry"]["status"] == "waiting"
    OFFSET[0] = timedelta(hours=49)
    e = await due_entry(api, student, N2)
    assert (e["status"], e["empty_reason"]) == ("unavailable", "all_excluded")
    assert (await api.next(student, e["entry_id"])).json() == {"status": "unavailable", "empty_reason": "all_excluded"}


@pytest.mark.parametrize("mode,reason", [("outside", "adapter_result_rejected"), ("not_admitted", "adapter_result_rejected"),
                                         ("empty", "no_admitted_candidate"), ("status501", "adapter_unavailable")])
async def test_adapter_result_checked(api, student, mode, reason):
    await api.grade(student, "SYNP-0001", "41")
    e = await due_entry(api, student)
    api.fakes.variation_mode = mode
    r = await api.next(student, e["entry_id"])
    assert r.status_code == 200 and r.json() == {"status": "unavailable", "empty_reason": reason}
    assert api.fakes.calls["generate_variation"] == 1


async def test_adapter_transient_failure_is_retryable(api, student):
    await api.grade(student, "SYNP-0001", "41")
    e = await due_entry(api, student)
    api.fakes.variation_mode = "busy"
    r = await api.next(student, e["entry_id"])
    assert r.status_code == 503 and r.json()["code"] == "UPSTREAM_UNAVAILABLE" and r.json()["retryable"] is True


# ---------- 票据与请求校验 ----------
async def test_ticket_forged_expired_cross_student_cross_subject(api, ppools, student):
    _, admin = ppools
    await api.grade(student, "SYNP-0001", "41")
    e = await due_entry(api, student)
    tok = (await api.next(student, e["entry_id"])).json()["ticket"]
    before = await counts(admin, student)
    other = str(uuid.uuid4())
    head, payload, sig = tok.split(".")
    t = T.verify(SECRET, tok, now=clock(), ttl_minutes=60)
    forged_other = T.issue("another-secret-another-secret-0000000001", t)
    cases = [
        (await api.answer(student, f"{head}.{payload}.{sig[::-1]}"), 403, "TICKET_INVALID"),
        (await api.answer(student, forged_other), 403, "TICKET_INVALID"),
        (await api.answer(other, tok), 403, "TICKET_INVALID"),                       # 跨学生
        (await api.answer(student, tok, subject="zhixing"), 403, "TICKET_INVALID"),   # 跨学科
    ]
    for r, status, code in cases:
        assert r.status_code == status and r.json()["code"] == code, r.text
    OFFSET[0] = timedelta(minutes=61)
    r = await api.answer(student, tok)
    assert r.status_code == 410 and r.json()["code"] == "TICKET_EXPIRED"
    assert await counts(admin, student) == before                                    # 一行不写
    # 计数键（审查 S11）：伪造与跨学生、跨学科都记 ticket_invalid，过期记 ticket_expired
    stats = api.svc.stats
    assert (stats["ticket_invalid"], stats["ticket_expired"]) == (4, 1)
    assert not any(k.startswith("ticket_ticket") for k in stats)


async def test_request_validation(api, student):
    await api.grade(student, "SYNP-0001", "41")
    e = await due_entry(api, student)
    tok = (await api.next(student, e["entry_id"])).json()["ticket"]
    for extra in ({"purpose": "retest"}, {"channel": "photo"}, {"hint_level_used": 3}, {"evidence_level": "A"}):
        r = await api.answer(student, tok, extra=extra)
        assert r.status_code == 400 and r.json()["code"] == "INVALID_REQUEST", extra
    assert (await api.answer(student, tok, key="not-a-uuid")).status_code == 400
    assert (await api.answer(student, tok, key=str(uuid.uuid4()).upper())).status_code == 400
    assert (await api.answer(student, tok, usage="x")).status_code == 400
    r = await api.answer(student, tok, answer={"choice": ["A"]})                  # 填空题给了选项
    assert r.status_code == 422 and r.json()["code"] == "INVALID_ANSWER"
    assert (await api.queue(student, "wenli")).json()["code"] == "SUBJECT_NOT_SUPPORTED"    # 语文默认关
    assert (await api.queue(student.upper())).status_code == 400
    r = await api.c.post("/internal/v1/practice/queue", content=b'{"student_ref": "a", "student_ref": "b", "subject": "aya"}')
    assert r.status_code == 400
    r = await api.c.post("/internal/v1/practice/queue", content=b"x" * (65 * 1024))
    assert r.status_code == 413
    assert (await api.next(student, str(uuid.uuid4()))).json()["code"] == "ENTRY_NOT_FOUND"
    # 别的学生拿这个条目 ID：同样 404
    assert (await api.next(str(uuid.uuid4()), e["entry_id"])).json()["code"] == "ENTRY_NOT_FOUND"


# ---------- 幂等 ----------
async def test_idempotent_replay(api, ppools, student):
    _, admin = ppools
    await api.grade(student, "SYNP-0001", "41")
    e = await due_entry(api, student)
    tok = (await api.next(student, e["entry_id"])).json()["ticket"]
    key, usage = str(uuid.uuid4()), str(uuid.uuid4())
    first = await api.answer(student, tok, "41", key=key, usage=usage)
    assert first.status_code == 200 and first.headers["idempotent-replayed"] == "false"
    c1 = await counts(admin, student)
    grades = api.fakes.calls["grade"]
    again = await api.answer(student, tok, "41", key=key, usage=usage)
    assert again.status_code == 200 and again.headers["idempotent-replayed"] == "true" and again.json() == first.json()
    assert await counts(admin, student) == c1 and api.fakes.calls["grade"] == grades       # 不写新行、不再判分
    # 同键过期后重试仍拿回原结果（结果未知时 BFF 用同一个键重试）
    OFFSET[0] = timedelta(minutes=90)
    late = await api.answer(student, tok, "41", key=key, usage=usage)
    assert late.status_code == 200 and late.headers["idempotent-replayed"] == "true"
    OFFSET[0] = timedelta(0)
    conflict = await api.answer(student, tok, "42", key=key, usage=usage)
    assert conflict.status_code == 409 and conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    # 同一张票据换一个键再答：这一轮已经答过（条目在等下一轮）
    r = await api.answer(student, tok, "42")
    assert r.status_code == 409 and r.json()["code"] == "PRACTICE_NOT_DUE"
    assert await counts(admin, student) == c1
    # 只查不执行的重放：既有的 GET /internal/v1/replay/grade/{key}
    rp = await api.c.get(f"/internal/v1/replay/grade/{key}", params={"student_ref": student, "usage_id": usage})
    assert rp.status_code == 200 and rp.json()["verdict"] == "incorrect"


# ---------- 同票据换键、并发、连接池（审查 B1、B2、H1、S1、S6） ----------
async def test_same_ticket_new_key_rejected_after_pending_review(api, ppools, student):
    """判分待复核：条目状态不变（不算错题也不闭环），只看条目会让同一张票据换个键再答一次；按“票据的题签发后已作答过”拒（B1）。
    返回体标明 pending_review，verdict 原样（S6）。"""
    _, admin = ppools
    await api.grade(student, "SYNP-0001", "41")
    e = await due_entry(api, student)
    tok = (await api.next(student, e["entry_id"])).json()["ticket"]
    r = await api.answer(student, tok, "review")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out == {"verdict": "incorrect", "pending_review": True,
                   "entry": {"entry_id": e["entry_id"], "status": "due", "round": 1, "next_round_at": None}}
    _validator("AnswerResult").validate(out)
    no_forbidden(out)
    c1 = await counts(admin, student)
    r = await api.answer(student, tok, "42")                                   # 同一张票据换一个键
    assert r.status_code == 409 and r.json()["code"] == "TICKET_USED", r.text
    assert await counts(admin, student) == c1 and api.svc.stats["ticket_used"] == 1
    # 重新出题拿新票据（做过的题已排除）照常作答；普通判分的返回里没有 pending_review
    n = (await api.next(student, e["entry_id"])).json()
    assert n["item_ref"] == "aya:SYNP-0004"
    a = (await api.answer(student, n["ticket"], "42")).json()
    assert a["entry"]["status"] == "closed" and "pending_review" not in a


async def test_concurrent_same_ticket_only_one_succeeds(api, ppools, student):
    """两个不同键并发用同一张票据：同一单元的作答串行，只有一个写入（另一个 503 BUSY 或 409）。"""
    _, admin = ppools
    await api.grade(student, "SYNP-0001", "41")
    e = await due_entry(api, student)
    tok = (await api.next(student, e["entry_id"])).json()["ticket"]
    c0 = await counts(admin, student)
    api.fakes.grade_delay = 0.5
    rs = await asyncio.wait_for(asyncio.gather(api.answer(student, tok, "41"), api.answer(student, tok, "41")), timeout=60)
    codes = sorted(r.status_code for r in rs)
    assert codes[0] == 200 and codes[1] in (409, 503), [r.text for r in rs]
    c1 = await counts(admin, student)
    assert c1["attempt"] == c0["attempt"] + 1 and c1["grade_result"] == c0["grade_result"] + 1


async def test_small_pool_concurrent_answers_do_not_hang(ppg, ppools, plock):
    """业务池上限 2、同时 3 个作答：锁连接不占业务池、取连接有上限，不会互相等死；每个作答成功或 503 BUSY（B2）。"""
    _, admin = ppools
    small = await asyncpg.create_pool(**ppg.dsn(LOGIN_ROLE), min_size=0, max_size=2)
    try:
        app, fakes = await make_api(small, plock, mastery=engine(small))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as c:
                c.headers["authorization"] = f"Bearer {CREDENTIAL}"
                a = Api(c, fakes, app)
                toks = []
                for _ in range(3):
                    s = await new_student(admin)
                    await a.grade(s, "SYNP-0001", "41")
                    e = await due_entry(a, s)
                    toks.append((s, (await a.next(s, e["entry_id"])).json()["ticket"]))
                fakes.grade_delay = 0.2
                rs = await asyncio.wait_for(asyncio.gather(*(a.answer(s, t, "42") for s, t in toks)), timeout=60)
                assert all(r.status_code in (200, 503) for r in rs), [r.text for r in rs]
                assert any(r.status_code == 200 for r in rs)
                for r in rs:
                    if r.status_code == 503:
                        assert r.json()["code"] == "BUSY" and r.json()["retryable"] is True
    finally:
        await small.close()


def test_key_state_settled_only_for_success_or_live_lease():
    """已提交键放行（H1）：只有同一学生的 succeeded，或 pending 且租约未过期，才放过过期票据、跳过条目确认。"""
    from learning.practice.store import KeyState
    s, other = str(uuid.uuid4()), str(uuid.uuid4())
    assert KeyState(s, "succeeded", False).settled_for(s)
    assert KeyState(s, "pending", True).settled_for(s)
    assert not KeyState(s, "pending", False).settled_for(s)
    assert not KeyState(s, "failed", False).settled_for(s)
    assert not KeyState(other, "succeeded", False).settled_for(s)


async def test_failed_or_stale_key_does_not_bypass_ticket_checks(api, ppools, student):
    """失败过、或 pending 租约已过期的键：照常验票（过期 410）；票据有效时照常确认条目并接管执行（H1）。"""
    _, admin = ppools
    await api.grade(student, "SYNP-0001", "41")
    e = await due_entry(api, student)
    tok = (await api.next(student, e["entry_id"])).json()["ticket"]
    key, usage = str(uuid.uuid4()), str(uuid.uuid4())
    api.fakes.grade_mode = "bad"
    r = await api.answer(student, tok, "42", key=key, usage=usage)
    assert r.status_code == 502, r.text
    api.fakes.grade_mode = "ok"
    async with admin.acquire() as c:
        assert await c.fetchval("SELECT status FROM platform.ingest_request WHERE idempotency_key = $1", key) == "failed"
    OFFSET[0] = timedelta(minutes=61)
    r = await api.answer(student, tok, "42", key=key, usage=usage)
    assert r.status_code == 410 and r.json()["code"] == "TICKET_EXPIRED", r.text
    async with admin.acquire() as c:
        await c.execute("""UPDATE platform.ingest_request SET status = 'pending', error_code = NULL, lease_token = gen_random_uuid(),
                                  lease_until = now() - interval '1 minute' WHERE idempotency_key = $1""", key)
    r = await api.answer(student, tok, "42", key=key, usage=usage)
    assert r.status_code == 410 and r.json()["code"] == "TICKET_EXPIRED", r.text
    OFFSET[0] = timedelta(0)
    r = await api.answer(student, tok, "42", key=key, usage=usage)
    assert r.status_code == 200 and r.json()["entry"]["status"] == "closed", r.text
    assert r.headers["idempotent-replayed"] == "false"


async def test_key_reread_after_taking_lock(api, monkeypatch, student):
    """拿锁之后再读一次键（S1）：锁外读到“没提交过”、等锁期间同键已成功时，交给幂等处理拿回原结果，而不是按条目状态 409。"""
    from learning.practice import answer as AN
    await api.grade(student, "SYNP-0001", "41")
    e = await due_entry(api, student)
    tok = (await api.next(student, e["entry_id"])).json()["ticket"]
    key, usage = str(uuid.uuid4()), str(uuid.uuid4())
    first = await api.answer(student, tok, "42", key=key, usage=usage)
    assert first.status_code == 200 and first.json()["entry"]["status"] == "closed"
    real, calls = AN._key_state, []

    async def stale(svc, k):
        calls.append(k)
        return None if len(calls) == 1 else await real(svc, k)
    monkeypatch.setattr(AN, "_key_state", stale)
    r = await api.answer(student, tok, "42", key=key, usage=usage)
    assert r.status_code == 200 and r.headers["idempotent-replayed"] == "true" and r.json() == first.json(), r.text
    assert len(calls) == 2


# ---------- 错因表、无原题、知识服务请求自检（审查 S4、S5、S8） ----------
async def test_taxonomy_unreadable_skips_error_category(api, monkeypatch, student):
    from learning.practice import select as S

    def broken():
        raise OSError("synthetic: taxonomy file missing")
    monkeypatch.setattr(S, "taxonomy_codes", broken)
    await api.grade(student, "SYNP-0001", "41")
    e = await due_entry(api, student)
    r = await api.next(student, e["entry_id"])
    assert r.status_code == 200 and r.json()["status"] == "ready", r.text
    assert "subject_error_category" not in api.fakes.variation_requests[-1]
    assert api.svc.stats["taxonomy_unavailable"] == 1


async def test_hint_followup_without_source_item_is_unavailable(api, ppools, student):
    """hint_followup 找不到提示后做对的那道题：不调知识服务与适配器，标 unavailable（no_source_item）。"""
    _, admin = ppools
    async with admin.acquire() as c:
        await c.execute("""INSERT INTO platform.retest_task (student_id, subject, node_id, cognitive_level, reason, due_at)
                           VALUES ($1::uuid, 'aya', $2, 'apply', 'hint_followup', now() - interval '1 minute')""", student, N2)
    e = await due_entry(api, student, N2)
    assert (e["source"], e["status"], e["empty_reason"], e["source_item_ref"]) == ("hint_followup", "unavailable", "no_source_item", None)
    _validator("Entry").validate(e)
    r = await api.next(student, e["entry_id"])
    assert r.status_code == 200 and r.json() == {"status": "unavailable", "empty_reason": "no_source_item"}
    _validator("NextUnavailable").validate(r.json())
    assert api.fakes.calls == {}                                            # 知识服务、适配器一次都没调


async def test_knowledge_request_self_check():
    """发送前按契约 ItemSimilarRequest / ItemSearchRequest 自检：不合契约的请求不发出（S8）。"""
    from learning.diagnosis.source import SourceError
    from learning.practice.queue import Unit
    from learning.practice.sources import KnowledgeCandidates, RequestMismatch
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(503, json={})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        k = KnowledgeCandidates(c, "http://knowledge.invalid", KNOWLEDGE.credential)
        with pytest.raises(RequestMismatch):
            await k.similar("aya", "bad id", "1.0.0")
        with pytest.raises(RequestMismatch):
            await k.search("aya", Unit(N1, "remember"), "1.0.0")
        assert sent == []
        with pytest.raises(SourceError):                                    # 合契约的请求照常发出
            await k.search("aya", Unit(N1, "apply"), "1.0.0")
        with pytest.raises(SourceError):
            await k.similar("aya", "SYNP-0001", "1.0.0")
    assert len(sent) == 2


async def test_recompute_failure_does_not_affect_answer(ppools, plock, student):
    app_pool, admin = ppools

    async def boom():
        raise RuntimeError("synthetic recompute failure")
    eng = engine(app_pool, after_read=boom)
    app, fakes = await make_api(app_pool, plock, mastery=eng)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as c:
            c.headers["authorization"] = f"Bearer {CREDENTIAL}"
            a = Api(c, fakes, app)
            await a.grade(student, "SYNP-0001", "41")
            failed = eng.stats["failed"]
            e = await due_entry(a, student)
            n = (await a.next(student, e["entry_id"])).json()
            r = await a.answer(student, n["ticket"], "42")
            assert r.status_code == 200 and r.json()["entry"]["status"] == "closed"
            assert eng.stats["failed"] > failed
    async with admin.acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM platform.mastery_estimate WHERE student_id = $1::uuid", student) == 0
        assert await c.fetchval("SELECT count(*) FROM platform.grade_result WHERE student_id = $1::uuid", student) == 2


# ---------- hint_followup 与复测标记（只读 retest_task） ----------
async def test_hint_followup_entry_closed_by_kge(api, ppools, student):
    _, admin = ppools
    s = await api.grade(student, "SYNP-0001", "42", hint=3)                # 高级提示后做对：KGE-04 排 hint_followup
    assert s["verdict"] == "correct"
    async with admin.acquire() as c:
        assert await c.fetchval("""SELECT count(*) FROM platform.retest_task WHERE student_id = $1::uuid AND reason = 'hint_followup'
                                     AND status = 'open'""", student) == 1
    e = await due_entry(api, student)
    assert (e["source"], e["status"], e["source_item_ref"]) == ("hint_followup", "due", "aya:SYNP-0001")
    n = (await api.next(student, e["entry_id"])).json()
    assert n["item_ref"] == "aya:SYNP-0003"
    assert "subject_error_category" not in api.fakes.variation_requests[-1]
    a = (await api.answer(student, n["ticket"], "42")).json()
    assert a["entry"]["status"] == "closed"
    async with admin.acquire() as c:
        st = await c.fetchval("SELECT status FROM platform.retest_task WHERE student_id = $1::uuid AND reason = 'hint_followup'", student)
    assert st != "open"                                                     # 由 KGE-04 重算关闭（本任务只读，不写）


async def _hint_task_status(admin, student) -> list[str]:
    async with admin.acquire() as c:
        return [r[0] for r in await c.fetch("""SELECT status FROM platform.retest_task WHERE student_id = $1::uuid
                                                 AND reason = 'hint_followup' ORDER BY created_at, id""", student)]


async def test_hint_followup_closing_matches_kge_diff_tasks(api, ppools, student):
    """对拍（审查 S10）：hint_followup 条目的闭环起点与 KGE-04 关闭任务（diff_tasks 撤销不再需要的任务）的口径一致：
    提示后做对之前的独立做对不算；之后另一族的独立做对，再练条目闭环的同时 KGE-04 关闭任务。全部经平台 grade 写入，不经再练接口。
    已知不一致（待人确认，未改任何一边）：起点之后与提示题同族的另一道题独立做对，再练这边闭环，KGE-04 记 repeat_family 不计入、
    任务仍开着；这里只对拍一致的部分。"""
    from learning.practice import queue as Q
    _, admin = ppools

    async def practice_view(task):
        """再练这边对同一个任务的判断（KGE-04 关闭任务后列表不再读它，所以用关闭前取到的任务按当前作答重算）。"""
        snap = await api.svc.snapshot(student, "aya", clock())
        return Q.hint_entry(task, snap.attempts, clock()).status

    await api.grade(student, "SYNP-0003", "42")                            # 提示之前的独立做对（fam-b）
    await api.grade(student, "SYNP-0001", "42", hint=3)                    # 高级提示后做对（fam-a）：排 hint_followup
    assert await _hint_task_status(admin, student) == ["open"]
    e = await due_entry(api, student)
    assert (e["source"], e["status"]) == ("hint_followup", "due")          # 起点之前的做对不闭环，两边一致
    task = (await api.svc.snapshot(student, "aya", clock())).hint_tasks[0]
    assert await practice_view(task) == "due"
    await api.grade(student, "SYNP-0004", "42")                            # 起点之后另一族（fam-c）独立做对
    assert await practice_view(task) == "closed"
    assert await _hint_task_status(admin, student) == ["cancelled"]       # KGE-04 同时关闭


async def test_retest_due_flag(api, ppools, student):
    _, admin = ppools
    # 两个不同题目族独立做对 → KGE-04 到“执行”并排当日复测 rung0（合成配置：2 小时后到期）
    await api.grade(student, "SYNP-0003", "42")
    await api.grade(student, "SYNP-0004", "42")
    await api.grade(student, "SYNP-0001", "41")
    async with admin.acquire() as c:
        due = await c.fetchval("""SELECT due_at FROM platform.retest_task WHERE student_id = $1::uuid AND node_id = $2
                                    AND reason = 'scheduled' AND status = 'open'""", student, N1)
    assert due is not None
    assert (await due_entry(api, student))["retest_due"] is False          # 还没到期
    OFFSET[0] = due - datetime.now(timezone.utc) + timedelta(minutes=1)
    e = await due_entry(api, student)
    assert e["retest_due"] is True and e["status"] == "due"


# ---------- 掌握不受本任务影响 ----------
async def test_mastery_identical_with_and_without_practice(api, ppools, student):
    """同一组作答：A 经再练接口作答，B 直接经 grade 写入；两人的掌握记录与复测任务逐字段一致（本任务只读、只经 grade 写）。"""
    _, admin = ppools
    b = str(uuid.uuid4())
    async with admin.acquire() as c:
        acct, guardian, household = (str(uuid.uuid4()) for _ in range(3))
        await c.execute("INSERT INTO platform.account (id, kind) VALUES ($1::uuid, 'guardian'), ($2::uuid, 'student')", guardian, acct)
        await c.execute("INSERT INTO platform.household (id, created_by_account) VALUES ($1::uuid, $2::uuid)", household, guardian)
        await c.execute("""INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month)
                           VALUES ($1::uuid, $2::uuid, $3::uuid, 9, 'nanjing', 'suke-2024', '2012-03-01')""", b, acct, household)
    await api.grade(student, "SYNP-0001", "41")
    await api.grade(b, "SYNP-0001", "41")
    e = await due_entry(api, student)
    for _ in range(3):                                                      # 反复读列表、出题：都不写
        await api.queue(b)
        await api.next(student, e["entry_id"])
    n = (await api.next(student, e["entry_id"])).json()
    await api.answer(student, n["ticket"], "42")
    await api.grade(b, n["item_ref"].split(":", 1)[1], "42")

    async def snap(sid):
        async with admin.acquire() as c:
            est = await c.fetch("""SELECT node_id, cognitive_level, state::text, p_mastery, confidence, next_retest_at IS NULL AS no_retest,
                                          cardinality(evidence_ids) AS n, algorithm_version::text, map_version_id::text, is_current
                                     FROM platform.mastery_estimate WHERE student_id = $1::uuid ORDER BY computed_at, id""", sid)
            tasks = await c.fetch("""SELECT node_id, cognitive_level, rung, reason, status FROM platform.retest_task
                                      WHERE student_id = $1::uuid ORDER BY created_at, id""", sid)
            att = await c.fetch("""SELECT item_id::text, channel, evidence_kind, evidence_level, hint_level_used FROM platform.attempt
                                    WHERE student_id = $1::uuid ORDER BY recorded_at""", sid)
        return [dict(r) for r in est], [dict(r) for r in tasks], [dict(r) for r in att]
    sa, sb = await snap(student), await snap(b)
    assert sa[0] and sa[0] == sb[0]
    assert sa[1] == sb[1] and sa[2] == sb[2]


# ---------- 鉴权与开关 ----------
async def test_only_bff_token(api, student):
    paths = [("/internal/v1/practice/queue", {"student_ref": student, "subject": "aya"}),
             ("/internal/v1/practice/next", {"student_ref": student, "subject": "aya", "entry_id": str(uuid.uuid4())}),
             ("/internal/v1/practice/answer", {"student_ref": student, "subject": "aya", "ticket": "x", "answer": {"text": "1"},
                                               "idempotency_key": str(uuid.uuid4()), "usage_id": str(uuid.uuid4())})]
    for path, body in paths:
        for header in ("", f"Bearer {ORCH}", f"Bearer {INSIGHT}", f"Bearer {ADAPTER_CREDENTIAL}", f"Bearer {KNOWLEDGE.credential}",
                       f"Bearer {SECRET}", f"Bearer {CREDENTIAL}x", CREDENTIAL):
            r = await api.c.post(path, json=body, headers={"authorization": header})
            assert r.status_code == 401 and r.json()["code"] == "UNAUTHENTICATED", (path, header)
    assert (await api.queue(student)).status_code == 200


async def test_not_configured_and_no_presenter(ppools, plock, student):
    app_pool, _ = ppools
    for over, codes in (({"practice_config": None}, (501, 501, 501)), ({"practice_presenter": None}, (200, 501, 501))):
        app, fakes = await make_api(app_pool, plock, **over)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as c:
                c.headers["authorization"] = f"Bearer {CREDENTIAL}"
                a = Api(c, fakes, app)
                got = ((await a.queue(student)).status_code, (await a.next(student, str(uuid.uuid4()))).status_code,
                       (await a.answer(student, "x")).status_code)
                assert got == codes, over


def test_synthetic_presenter_and_config_need_test_env():
    from learning.config import Settings
    from learning.practice import config as PC
    from learning.practice.presenter import SyntheticPracticePresenter
    with pytest.raises(RuntimeError):
        SyntheticPracticePresenter("dev")
    base = {"LEARNING_ENV": "test", "LEARNING_SERVICE_TOKEN": CREDENTIAL, "LEARNING_DATABASE_URL": "postgresql://unused",
            "LEARNING_PRACTICE_CONFIG": "synthetic", "LEARNING_PRACTICE_TICKET_SECRET": SECRET,
            "LEARNING_KNOWLEDGE_URL": "http://127.0.0.1:8790", "LEARNING_KNOWLEDGE_TOKEN": KNOWLEDGE.credential,
            "LEARNING_PRACTICE_PRESENTER": "synthetic"}
    s = Settings.from_env(base)
    assert s.practice_config == "synthetic" and s.knowledge is not None and s.practice_presenter == "synthetic"
    for bad in ({"LEARNING_PRACTICE_TICKET_SECRET": ""}, {"LEARNING_PRACTICE_TICKET_SECRET": "short"},
                {"LEARNING_PRACTICE_TICKET_SECRET": CREDENTIAL},                       # 与服务令牌相同
                {"LEARNING_ENV": "dev"},                                               # 合成题面只在 test
                {"LEARNING_PRACTICE_PRESENTER": "real"}):
        with pytest.raises(RuntimeError):
            Settings.from_env({**base, **bad})
    # 未签字配置在 test / dev 以外只是这一模块不启动（501），不是整个服务拒绝启动
    with pytest.raises(PC.PracticeConfigUnsigned):
        PC.load("synthetic", env="pilot")


async def test_synthetic_presenter_checks_database_name():
    from learning.app import _check_practice_env

    class FakePool:
        def __init__(self, name):
            self.name = name

        async def fetchval(self, _sql):
            return self.name
    await _check_practice_env(FakePool("starbuddy_test"), True)
    for bad in ("starbuddy", "starbuddy_pilot", "prod_starbuddy_test"):
        with pytest.raises(RuntimeError):
            await _check_practice_env(FakePool(bad), True)
    await _check_practice_env(FakePool("anything"), False)


# ---------- 服务目录 OpenAPI 一致性（沿 SIE-02a 先例） ----------
def test_internal_openapi_matches_api_keys():
    import yaml
    doc = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    src = (Path(__file__).resolve().parents[1] / "learning/practice/api.py").read_text(encoding="utf-8")
    routes = re.findall(r'@app\.post\("([^"]+)"\)(.*?)(?=@app\.|\Z)', src, re.S)
    assert {p for p, _ in routes} == set(doc["paths"]) == {"/internal/v1/practice/queue", "/internal/v1/practice/next",
                                                           "/internal/v1/practice/answer"}
    for path, body in routes:
        m = re.search(r"_keys\(b, (\{[^}]*\})(?:, (\{[^}]*\}))?\)", body)
        assert m, path
        req = set(eval(m.group(1)))  # noqa: S307 源码里的集合字面量
        opt = set(eval(m.group(2))) if m.group(2) else set()
        schema = doc["paths"][path]["post"]["requestBody"]["content"]["application/json"]["schema"]
        assert set(schema["required"]) == req and set(schema["properties"]) == req | opt, path
        assert schema["additionalProperties"] is False, path
    # 重放不另建：文档写明用既有的 GET /internal/v1/replay/grade/{key}
    assert "/internal/v1/replay/grade/" in doc["info"]["description"]


def _validator(name: str):
    import jsonschema
    import yaml
    doc = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator({"$ref": f"#/components/schemas/{name}", "components": doc["components"]})


async def test_internal_openapi_matches_responses(api, student):
    await api.grade(student, "SYNP-0001", "41")
    await api.grade(student, "SYNP-0008", "41")
    q = (await api.queue(student)).json()
    assert len(q["entries"]) == 2
    for e in q["entries"]:
        _validator("Entry").validate(e)
    by_node = {e["node_id"]: e for e in q["entries"]}
    n = (await api.next(student, by_node[N1]["entry_id"])).json()
    _validator("NextReady").validate(n)
    _validator("NextUnavailable").validate((await api.next(student, by_node[N3]["entry_id"])).json())
    a = (await api.answer(student, n["ticket"], "41")).json()
    _validator("AnswerResult").validate(a)
    assert a["entry"]["next_round_at"] is not None
