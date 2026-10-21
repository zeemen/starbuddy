"""测试夹具。全部是合成数据（CLAUDE.md 规则 8）：学生、家庭、题目都是占位 ID。

- pg：会话级的一次性本地 Postgres，已执行角色 bootstrap 与全部迁移，并登记了几道合成题
- fake_adapter：按契约形状返回结果的假适配器（httpx.MockTransport），可以篡改结果、注入错误、计数
- api：进程内的学习服务（FastAPI + ASGITransport），连接用只继承 starbuddy_learning 的登录角色
- 计量（LT-04）：/internal/v1 写入前学习服务经 0058 的 platform.learning_meter_check 核对计量。生产里预留与计次由 BFF 建；
  这里的测试不经 BFF，所以自动夹具 meter_provisioned 在每次核对前由测试库里的 learningtest.provision_meter（fixtures/meter_provision.sql）
  按核对依据代建一笔（直接写入与诊断会话：预留中的预留；再练、复测：自练计次），核对本身仍是真实函数。
  代建一律经超级用户连接池（register_admin 登记的、与应用池同一个测试库的 admin 池）：夹具函数不授给 starbuddy_learning，
  学习服务角色自己不能造预留（安全审查阻断）。测核对本身的用例标 @pytest.mark.real_meter，不代建，记录由用例自己写（meter_row）。
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import asyncpg
import httpx
import pytest

from pathlib import Path

from learning import meter as learning_meter
from learning.app import create_app
from learning.config import AdapterTarget, Settings
from pgharness import LOGIN_ROLE, EphemeralPG

METER_PROVISION_SQL = (Path(__file__).resolve().parent / "fixtures/meter_provision.sql").read_text(encoding="utf-8")
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
INSERT INTO platform.item (subject, item_id, curriculum_version_id, skill_local_id, admission, source_ref, source_type, license_status) VALUES
  ('aya', 'SYN-UNV', 'e0000000-0000-4000-8000-0000000000e0', 'K-SYN-0001', 'source_unverified', NULL, 'unknown', 'unverified'),
  ('aya', 'SYN-ADM', 'e0000000-0000-4000-8000-0000000000e0', 'K-SYN-0001', 'pending_review', 'SYN-SRC-0001', 'self_authored', 'cleared'),
  ('aya', 'SYN-UNV2', 'e0000000-0000-4000-8000-0000000000e0', 'K-SYN-0002', 'source_unverified', NULL, 'unknown', 'unverified');
-- 准入只能经发布闸门（迁移 0039）：合成 staff 签准入审核并执行，SYN-ADM 升为 admitted
INSERT INTO platform.account (id, kind) VALUES ('5b8e1c52-0000-4000-8000-0000000005f1', 'staff');
SET ROLE starbuddy_review;
SELECT set_config('app.account_id', '5b8e1c52-0000-4000-8000-0000000005f1', false);
SELECT platform.change_admission(ARRAY[platform.sign_admission('aya', 'SYN-ADM', 'admitted', 'approved', 'Q2', NULL, NULL, 'cleared',
                                                               platform.item_admission_digest('aya', 'SYN-ADM'), 'test_fixture')]);
SELECT set_config('app.account_id', '', false);
RESET ROLE;
""" + METER_PROVISION_SQL

_REAL_REQUIRE = learning_meter.require
# 应用池（learning_app，只继承 starbuddy_learning）→ 同一测试库的超级用户池；代建只经后者（夹具函数不授学习服务角色）
_ADMIN_FOR: dict[int, asyncpg.Pool] = {}


def register_admin(app_pool, admin) -> None:
    _ADMIN_FOR[id(app_pool)] = admin


def unregister_admin(app_pool) -> None:
    _ADMIN_FOR.pop(id(app_pool), None)


class MeterProvision:
    """代建开关：on = True 时每次核对前代建；用例可在准备数据之后关掉（meter_provisioned.on = False）再测真实核对。"""

    def __init__(self, on: bool) -> None:
        self.on = on

    async def require(self, pool, basis, *, method, ctx):
        """先在测试库里按核对依据代建计量记录（BFF 的那一步，经该库的超级用户池），再以学习服务的应用池走真实核对。
        应用池没有登记 admin 池或代建失败（库里没有夹具函数、参数不是 UUID 等）就跳过，交给真实核对判定（none → 409）。"""
        admin = _ADMIN_FOR.get(id(pool))
        if self.on and admin is not None:
            usage = basis.usage_id if basis.usage_id is not None else ctx.get("usage_id")
            key = basis.key if basis.key is not None else ctx.get("idempotency_key")
            try:
                async with admin.acquire() as conn:
                    await conn.execute("SELECT learningtest.provision_meter($1::text::uuid, $2, $3::text::uuid, $4, $5)",
                                       usage, key, ctx.get("student_ref"), basis.method or method, "counted" in basis.allowed)
            except Exception:  # noqa: BLE001
                pass
        await _REAL_REQUIRE(pool, basis, method=method, ctx=ctx)


@pytest.fixture(autouse=True)
def meter_provisioned(request, monkeypatch) -> MeterProvision:
    p = MeterProvision(on=request.node.get_closest_marker("real_meter") is None)
    monkeypatch.setattr(learning_meter, "require", p.require)
    return p


async def meter_row(admin, *, usage_id: str, key: str, student: str, method: str = "grade", state: str = "reserved") -> None:
    """real_meter 用例写计量记录：state = reserved / lapsed / settled / released / expired（预留）或 counted（自练计次）。
    lapsed 与 expired 的预留有效期在过去（9 分钟前到 1 分钟前，与过期任务的真实前提一致）；其余的有效期从现在起 10 分钟。"""
    async with admin.acquire() as c:
        if state == "counted":
            await c.execute("SELECT learningtest.provision_meter($1::uuid, $2, $3::uuid, $4, true)", usage_id, key, student, method)
            return
        await c.execute("SELECT learningtest.provision_meter($1::uuid, $2, $3::uuid, $4, false, $5)", usage_id, key, student, method,
                        state in ("lapsed", "expired"))
        if state not in ("reserved", "lapsed"):
            await c.execute("SELECT learningtest.set_reservation($1::uuid, $2)", usage_id, state)


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
    register_admin(app_pool, admin)
    yield app_pool, admin
    unregister_admin(app_pool)
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


RESPONSE_EVIDENCE_KINDS = ("response_text", "response_choice", "asr_transcription")


def _answer_in_version(body: dict, out: dict) -> dict:
    """按请求声明的契约版本应答；v1.0 下作答类证据带 attempt_id（评审 N3）。"""
    version = body["context"]["contract_version"]
    out["contract_version"] = version
    if version != "0.9.0":
        attempts = [body["attempt"]] if "attempt" in body else body["attempts"]
        by_ref = {(a["item_ref"], a["occurred_at"]): a["attempt_id"] for a in attempts}
        for e in out["evidence"]:
            if e["kind"] in RESPONSE_EVIDENCE_KINDS:
                e["attempt_id"] = by_ref[(e["item_ref"], e["occurred_at"])]
    return out


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
        out = _answer_in_version(body, out)
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


class InProcessResponse:
    """进程内调用的结果：取 httpx.Response 常用的那几样（status_code、json()、text、headers）。"""

    def __init__(self, status_code: int, body: dict, headers: dict | None = None) -> None:
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}

    def json(self) -> dict:
        return copy.deepcopy(self._body)

    @property
    def text(self) -> str:
        return json.dumps(self._body, ensure_ascii=False)


@asynccontextmanager
async def legacy_v0(app_pool, fake_adapter: FakeAdapter, **over):
    """插件契约 0.9.0 的写入，进程内调用（LT-04 决定记录（再续）第 1 条：/internal/v0 写入已从 HTTP 下线，一律 410）。
    v0 / v1 共用的写入核心（幂等、租约、落库映射、平台核对）仍按 0.9.0 请求体测；错误映射与 app.handle 相同
    （IngestError → 问题体；其余异常 → 500 INTERNAL、可重试）。0.9.0 不核对计量（不经 /internal/v1）。"""
    from learning.app import UUID_RE
    from learning.errors import IngestError
    from learning.service import IngestService

    client = httpx.AsyncClient(transport=httpx.MockTransport(fake_adapter.handler), trust_env=False, follow_redirects=False)
    svc = IngestService(settings(**over), app_pool, client)

    async def call(method: str, body: object) -> InProcessResponse:
        wire = json.loads(json.dumps(body))  # 与 HTTP 一样拿到一份独立的请求体
        ctx = wire.get("context") if isinstance(wire, dict) else None
        rid = ctx.get("request_id") if isinstance(ctx, dict) else None
        rid = rid if isinstance(rid, str) and UUID_RE.fullmatch(rid) else None
        try:
            summary, replayed = await svc.ingest(method, wire, "0.9.0")
            return InProcessResponse(200, summary, {"idempotent-replayed": "true" if replayed else "false"})
        except IngestError as e:
            return InProcessResponse(e.status, e.problem(rid))
        except Exception:  # noqa: BLE001
            return InProcessResponse(500, IngestError(500, "INTERNAL", "internal error", retryable=True).problem(rid))
    try:
        yield call
    finally:
        await client.aclose()


@pytest.fixture
async def v0(pools, fake_adapter):
    """进程内的 0.9.0 写入（见 legacy_v0）：v0(method, body) → InProcessResponse。"""
    async with legacy_v0(pools[0], fake_adapter) as call:
        yield call


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


def to_v1_body(body: dict) -> dict:
    """同一作答的 v1.0 写法：契约版本 1.0.0、UUIDv4 幂等键、平台生成的 attempt_id。"""
    b = copy.deepcopy(body)
    b["context"].update(contract_version="1.0.0", idempotency_key=str(uuid.uuid4()))
    for a in [b["attempt"]] if "attempt" in b else b["attempts"]:
        a["attempt_id"] = str(uuid.uuid4())
    return b


def env(request: dict, **ingest) -> dict:
    """learning-internal v1 的信封：插件请求加平台侧作答上下文（缺省键盘作答）。"""
    return {"request": request, "ingest": {"channel": "keypad", **ingest}}


def diagnose_body(student_id: str, attempts: list[dict]) -> dict:
    b = grade_body(student_id)
    del b["attempt"], b["rubric_version"]
    b["mode"] = "intake"
    b["attempts"] = attempts
    return b


def at(item: str, text: str, minute: int) -> dict:
    t = datetime(2026, 10, 8, 11, minute, tzinfo=timezone.utc).isoformat()
    return {"item_ref": f"aya:{item}", "occurred_at": t, "response": {"text": text}}
