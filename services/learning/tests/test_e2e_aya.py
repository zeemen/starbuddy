"""端到端（spec 验收）：学习服务 → 阿鸦适配器（真实 HTTP 进程）→ 平台库。

阿鸦的内容用 adapters/aya/scripts/register-content.ts 生成的 SQL 登记；适配器用 node 直接运行 TS 源码。
没有 node 或没装阿鸦依赖时跳过（LEARNING_TEST_STRICT=1 时失败）。
"""
from __future__ import annotations

import copy
import os
import shutil
import socket
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import pytest

from conftest import CREDENTIAL, at, counts, diagnose_body, grade_body
from conftest import settings as make_settings
from learning.app import create_app
from learning.config import AdapterTarget
from review_loop import close_loop, console

AYA = Path(__file__).resolve().parents[3] / "adapters/aya"
AYA_CREDENTIAL = "synthetic-learning-to-aya-credential-000001"


def _missing() -> str | None:
    if not shutil.which("node"):
        return "没有 node"
    if not (AYA / "node_modules/ajv").exists():
        return "没有安装阿鸦适配器依赖（cd adapters/aya && npm ci）"
    return None


@pytest.fixture(scope="module")
def aya_adapter(pg):
    why = _missing()
    if why:
        if os.environ.get("LEARNING_TEST_STRICT") == "1":
            pytest.fail(why)
        pytest.skip(why)
    sql = subprocess.run(["node", "--no-warnings", "scripts/register-content.ts"], cwd=AYA, check=True, capture_output=True).stdout
    pg.psql(sql.decode())
    pg.psql(sql.decode())  # 重复执行：只增不改，不应重复登记（原阿鸦 e2e 的断言迁到这里）
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ)
    env["AYA_ADAPTER_SERVICE_TOKEN"] = AYA_CREDENTIAL
    env["PORT"] = str(port)
    proc = subprocess.Popen(["node", "--no-warnings", "src/main.ts"], cwd=AYA, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 20
    while True:
        try:
            ok = httpx.get(f"http://127.0.0.1:{port}/plugin/v0/capabilities",
                           headers={"authorization": f"Bearer {AYA_CREDENTIAL}"}).status_code == 200
        except httpx.TransportError:
            ok = False
        if ok:
            break
        if time.time() > deadline:
            proc.kill()
            pytest.fail("阿鸦适配器没有启动")
        time.sleep(0.2)
    yield f"http://127.0.0.1:{port}"
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture
async def e2e_api(pools, aya_adapter):
    app_pool, _ = pools
    app = create_app(make_settings(adapters={"aya": AdapterTarget(aya_adapter, AYA_CREDENTIAL)}), pool=app_pool)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as c:
            c.headers["authorization"] = f"Bearer {CREDENTIAL}"
            yield c


async def test_content_registration_is_complete_and_idempotent(aya_adapter, pools):
    _, admin = pools
    async with admin.acquire() as c:
        got = await c.fetchrow(
            """SELECT (SELECT count(*) FROM platform.curriculum_version WHERE subject = 'aya' AND textbook_version = 'suke-2024') AS cv,
                      (SELECT count(*) FROM platform.skill_node s JOIN platform.curriculum_version v ON v.id = s.curriculum_version_id
                        WHERE v.textbook_version = 'suke-2024') AS nodes,
                      (SELECT count(*) FROM platform.item i JOIN platform.curriculum_version v ON v.id = i.curriculum_version_id
                        WHERE v.textbook_version = 'suke-2024') AS items,
                      (SELECT count(*) FROM platform.item i JOIN platform.curriculum_version v ON v.id = i.curriculum_version_id
                        WHERE v.textbook_version = 'suke-2024' AND i.admission = 'source_unverified') AS unverified""")
    assert tuple(got) == (1, 959, 721, 721)


async def test_grade_through_real_aya_adapter(e2e_api, pools, student, pg):
    _, admin = pools
    # (x-2)^2=49 答成 “9”：命中预标注陷阱，产出错因
    body = grade_body(student, item="Q-EQU-U03-D-001", text="9")
    r = await e2e_api.post("/internal/v0/grade", json=body)
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["verdict"] == "incorrect" and s["semantics"] == "practice" and s["human_review_required"]
    assert s["error_categories"]
    assert await counts(admin, student) == {"evidence_event": 2, "attempt": 1, "grade_result": 1, "grade_dimension": 1,
                                            "error_attribution": 1, "skill_observation": 0}
    async with admin.acquire() as c:
        ev = await c.fetch("SELECT evidence_id::text, admission::text FROM platform.evidence_event WHERE student_id = $1::uuid", student)
        assert {e["evidence_id"] for e in ev} == set(s["evidence_ids"])
        assert {e["admission"] for e in ev} == {"source_unverified"}
        g = await c.fetchrow("SELECT model_version::text, verifier_id, rubric_version::text FROM platform.grade_result WHERE id = $1::uuid",
                             s["grade_result_id"])
        assert tuple(g) == ("algo:aya-symbolic-ts@0.1.0", "aya-symbolic-ts", "aya-symbolic-ts@0.1.0")
        attr = await c.fetchrow("SELECT algorithm_version::text, evidence_ids::text[] FROM platform.error_attribution WHERE student_id = $1::uuid",
                                student)
        assert attr["algorithm_version"] == "aya-trap-attr@0.1.0-tax0.9.0"
        assert set(attr["evidence_ids"]) <= set(s["evidence_ids"])
    # 同 key 重试：同一批证据，不再入库
    retry = copy.deepcopy(body)
    retry["context"]["request_id"] = str(uuid.uuid4())
    r2 = await e2e_api.post("/internal/v0/grade", json=retry)
    assert r2.headers["idempotent-replayed"] == "true" and r2.json()["evidence_ids"] == s["evidence_ids"]
    assert (await counts(admin, student))["evidence_event"] == 2
    # 统一复核队列闭环（冲刺 2-G）：同一事务进队，重放不重复，staff 经审核台领取、结案
    async with admin.acquire() as c:
        reasons = set(await c.fetchval("SELECT review_reasons::text[] FROM platform.grade_result WHERE id = $1::uuid", s["grade_result_id"]))
    async with console(pg) as client:
        await close_loop(client, source_id=s["grade_result_id"], student_id=student, subject="aya", expect_reasons=reasons)


async def test_diagnose_through_real_aya_adapter(e2e_api, pools, student):
    _, admin = pools
    attempts = [at("Q-EQU-U03-D-001", "9,-5", 1), at("Q-EQU-U03-D-001", "7,-7", 2)]
    attempts[0]["self_report"] = "guessed"
    r = await e2e_api.post("/internal/v0/diagnose", json=diagnose_body(student, attempts))
    assert r.status_code == 200, r.text
    s = r.json()
    async with admin.acquire() as c:
        obs = await c.fetch(
            "SELECT skill_local_id::text, outcome, confidence::float8 AS conf FROM platform.skill_observation WHERE student_id = $1::uuid ORDER BY outcome",
            student)
        assert [(o["skill_local_id"], o["outcome"]) for o in obs] == [("S-EQU-0912", "demonstrated"), ("S-EQU-0912", "not_demonstrated")]
        assert obs[0]["conf"] <= 0.3  # 答对但自评“猜的”
        assert await c.fetchval("SELECT count(*) FROM platform.attempt WHERE student_id = $1::uuid", student) == 2
        assert s["observation_count"] == 2
        closed = await c.fetchval(
            """SELECT bool_and(EXISTS (SELECT 1 FROM platform.evidence_event e WHERE e.evidence_id = x))
                 FROM platform.skill_observation o, unnest(o.evidence_ids) x WHERE o.student_id = $1::uuid""", student)
        assert closed
