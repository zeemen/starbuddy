"""测试夹具。全部是合成数据（CLAUDE.md 规则 8）：学生、家庭、题目都是占位 ID。

- pg：会话级的一次性本地 Postgres，已执行角色 bootstrap 与全部迁移，并登记了几道合成题
- fake_adapter：按契约形状返回结果的假适配器（httpx.MockTransport），可以篡改结果、注入错误、计数
- api：进程内的学习服务（FastAPI + ASGITransport），连接用只继承 starbuddy_learning 的登录角色
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import uuid
from datetime import datetime, timezone

import asyncpg
import httpx
import pytest

from learning.app import create_app
from learning.config import AdapterTarget, Settings
from pgharness import LOGIN_ROLE, EphemeralPG

CREDENTIAL = "synthetic-bff-to-learning-credential-0001"
ADAPTER_CREDENTIAL = "synthetic-learning-to-adapter-credential-01"
NS = uuid.UUID("5b8e1c52-0000-4000-8000-000000000000")
CV = {"region": "nanjing", "exam_year": 2027, "grade": 9, "textbook_version": "suke-2024", "subject": "aya", "content_version": "0.1.0"}

FIXTURE_SQL = """
INSERT INTO platform.curriculum_version (id, region, exam_year, grade, textbook_version, subject, content_version)
VALUES ('e0000000-0000-4000-8000-0000000000e0', 'nanjing', 2027, 9, 'syn-2024', 'aya', '0.0.1');
INSERT INTO platform.skill_node (subject, local_id, curriculum_version_id, label_code) VALUES
  ('aya', 'K-SYN-0001', 'e0000000-0000-4000-8000-0000000000e0', 'K-SYN-0001'),
  ('aya', 'K-SYN-0002', 'e0000000-0000-4000-8000-0000000000e0', 'K-SYN-0002');
INSERT INTO platform.item (subject, item_id, curriculum_version_id, skill_local_id, admission, admitted_at) VALUES
  ('aya', 'SYN-UNV', 'e0000000-0000-4000-8000-0000000000e0', 'K-SYN-0001', 'source_unverified', NULL),
  ('aya', 'SYN-ADM', 'e0000000-0000-4000-8000-0000000000e0', 'K-SYN-0001', 'admitted', now()),
  ('aya', 'SYN-UNV2', 'e0000000-0000-4000-8000-0000000000e0', 'K-SYN-0002', 'source_unverified', NULL);
"""


@pytest.fixture(scope="session")
def pg():
    try:
        db = EphemeralPG()
    except FileNotFoundError as e:
        if os.environ.get("LEARNING_TEST_STRICT") == "1":
            raise
        pytest.skip(str(e))
    db.start()
    db.psql(FIXTURE_SQL)
    yield db
    db.stop()


@pytest.fixture(scope="session")
async def pools(pg):
    app_pool = await asyncpg.create_pool(**pg.dsn(LOGIN_ROLE), min_size=1, max_size=30)
    admin = await asyncpg.create_pool(**pg.dsn("postgres"), min_size=1, max_size=3)
    yield app_pool, admin
    await app_pool.close()
    await admin.close()


@pytest.fixture
async def student(pools) -> str:
    """每个测试一个新学生，行数按学生统计，互不干扰。"""
    _, admin = pools
    acct, guardian, household, sid = (str(uuid.uuid4()) for _ in range(4))
    async with admin.acquire() as c:
        await c.execute("INSERT INTO platform.account (id, kind) VALUES ($1::uuid, 'guardian'), ($2::uuid, 'student')", guardian, acct)
        await c.execute("INSERT INTO platform.household (id, created_by_account) VALUES ($1::uuid, $2::uuid)", household, guardian)
        await c.execute(
            """INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month)
               VALUES ($1::uuid, $2::uuid, $3::uuid, 9, 'nanjing', 'suke-2024', '2012-03-01')""",
            sid, acct, household,
        )
    return sid


TABLES = ("evidence_event", "attempt", "grade_result", "error_attribution", "skill_observation")


async def counts(admin, student_id: str) -> dict:
    async with admin.acquire() as c:
        out = {t: await c.fetchval(f"SELECT count(*) FROM platform.{t} WHERE student_id = $1::uuid", student_id) for t in TABLES}
        out["grade_dimension"] = await c.fetchval(
            """SELECT count(*) FROM platform.grade_dimension d JOIN platform.grade_result g ON g.id = d.grade_result_id
                WHERE g.student_id = $1::uuid""", student_id)
    return out


# ---------- 假适配器 ----------
def eid(key: str, attempt_id: str, label: str) -> str:
    return str(uuid.uuid5(NS, f"{key}|{attempt_id}|{label}"))


def _evidence(key, a, admission, node="aya:K-SYN-0001"):
    common = {"occurred_at": a["occurred_at"], "item_ref": a["item_ref"], "skill_node": node, "admission": admission}
    resp = {"evidence_id": eid(key, a["attempt_id"], "response"), "kind": "response_text", **common,
            "payload": {"text": a["response"].get("text", "")}}
    trace = {"evidence_id": eid(key, a["attempt_id"], "trace"), "kind": "verifier_trace", **common,
             "payload": {"sampled_points": 8, "agreeing_points": 8, "reason_codes": []}}
    return resp, trace


def _envelope(evidence, admission, extra_reasons=()):
    reasons = list(extra_reasons)
    if admission != "admitted":
        reasons.append("content_not_admitted")
    env = {
        "contract_version": "0.9.0",
        "evidence": evidence,
        "evidence_ids": [e["evidence_id"] for e in evidence],
        "confidence": 1,
        "source_version": "verifier:aya-symbolic-ts@0.1.0;items:syn-pack@0.0.1",
        "model_version": "algo:aya-symbolic-ts@0.1.0",
        "human_review_required": bool(reasons),
        "semantics": "formal" if admission == "admitted" else "practice",
    }
    if reasons:
        env["review_reasons"] = reasons
    return env


def fake_grade(body: dict, admission: str) -> dict:
    a, key = body["attempt"], body["context"]["idempotency_key"]
    correct = a["response"].get("text") == "42"
    resp, trace = _evidence(key, a, admission)
    attrs = [] if correct else [{
        "skill_node": "aya:K-SYN-0001", "subject_category": "yunsuan_cuowu", "cross_subject_category": "computation_or_grammar",
        "confidence": 0.8, "evidence_ids": [resp["evidence_id"]], "algorithm_version": "aya-trap-attr@0.1.0-tax0.9.0"}]
    return {
        **_envelope([resp, trace], admission),
        "result": {
            "verdict": "correct" if correct else "incorrect",
            "rubric_version": body["rubric_version"],
            "score": {"total": 1 if correct else 0, "max_total": 1, "calibrated": False},
            "dimensions": [{"dimension_id": "answer", "score": 1 if correct else 0, "max_score": 1, "judged_by": "verifier",
                            "subjective": False, "rule_ref": "aya-symbolic-ts@0.1.0", "confidence": 1,
                            "evidence_ids": [resp["evidence_id"], trace["evidence_id"]]}],
            "error_attributions": attrs,
            "verifier": {"verifier_id": "aya-symbolic-ts", "version": "0.1.0", "reason_codes": []},
        },
    }


def fake_diagnose(body: dict, admission: str) -> dict:
    key = body["context"]["idempotency_key"]
    evidence, observations = [], []
    for a in body["attempts"]:
        node = "aya:K-SYN-0002" if a["item_ref"] == "aya:SYN-UNV2" else "aya:K-SYN-0001"
        resp, trace = _evidence(key, a, admission, node)
        evidence += [resp, trace]
        observations.append({"skill_node": node, "outcome": "demonstrated" if a["response"].get("text") == "42" else "not_demonstrated",
                             "confidence": 1, "evidence_ids": [resp["evidence_id"], trace["evidence_id"]],
                             "algorithm_version": "aya-diag@0.1.0"})
    evidence.reverse()  # 故意打乱顺序：学习服务不能依赖证据的数组顺序
    return {**_envelope(evidence, admission), "result": {"observations": observations, "error_attributions": []},
            "model_version": "algo:aya-diag@0.1.0"}


class FakeAdapter:
    """按契约形状返回；可以篡改（mutate）、改状态码（status）、延时（delay）、先失败几次（fail_first）。"""

    def __init__(self) -> None:
        self.calls = 0
        self.admission = "source_unverified"
        self.mutate = None
        self.status = 200
        self.problem = None
        self.delay = 0.0
        self.connect_failures = 0

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        assert request.headers["authorization"] == f"Bearer {ADAPTER_CREDENTIAL}"
        if self.connect_failures > 0:
            self.connect_failures -= 1
            raise httpx.ConnectError("synthetic connect failure")
        if self.delay:
            await asyncio.sleep(self.delay)
        body = json.loads(request.content)
        if self.status != 200:
            return httpx.Response(self.status, json=self.problem or {})
        method = request.url.path.rsplit("/", 1)[-1]
        out = fake_grade(body, self.admission) if method == "grade" else fake_diagnose(body, self.admission)
        if self.mutate:
            out = self.mutate(copy.deepcopy(out)) or out
        # 原样编码（允许 NaN），模拟有缺陷的适配器
        return httpx.Response(200, content=json.dumps(out).encode(), headers={"content-type": "application/json"})


@pytest.fixture
def fake_adapter() -> FakeAdapter:
    return FakeAdapter()


def settings(**over) -> Settings:
    base = dict(env="test", database_url="unused", service_credential=CREDENTIAL,
                adapters={"aya": AdapterTarget("http://aya-adapter.invalid", ADAPTER_CREDENTIAL)})
    base.update(over)
    return Settings(**base)


@pytest.fixture
async def api(pools, fake_adapter):
    app_pool, _ = pools
    app = create_app(settings(), pool=app_pool, transport=httpx.MockTransport(fake_adapter.handler))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as client:
            client.headers["authorization"] = f"Bearer {CREDENTIAL}"
            yield client


_seq = [0]


def grade_body(student_id: str, item: str = "SYN-UNV", text: str = "41", **over) -> dict:
    _seq[0] += 1
    n = _seq[0]
    body = {
        "context": {
            "request_id": str(uuid.uuid4()), "contract_version": "0.9.0", "subject": "aya", "student_ref": student_id,
            "data_class": "L2", "usage_id": str(uuid.uuid4()), "idempotency_key": f"synthetic-learning-key-{uuid.uuid4().hex[:12]}-{n}",
            "content_version": dict(CV),
        },
        "attempt": {"item_ref": f"aya:{item}", "occurred_at": "2026-10-08T19:30:00+08:00", "response": {"text": text},
                    "elapsed_sec": 60, "hint_level_used": 0},
        "rubric_version": "aya-symbolic-ts@0.1.0",
    }
    body.update(over)
    return body


def diagnose_body(student_id: str, attempts: list[dict]) -> dict:
    b = grade_body(student_id)
    del b["attempt"], b["rubric_version"]
    b["mode"] = "intake"
    b["attempts"] = attempts
    return b


def at(item: str, text: str, minute: int) -> dict:
    t = datetime(2026, 10, 8, 11, minute, tzinfo=timezone.utc).isoformat()
    return {"item_ref": f"aya:{item}", "occurred_at": t, "response": {"text": text}}
