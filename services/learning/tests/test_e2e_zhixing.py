"""端到端（ADP-道法-01 验收）：学习服务 → 知行适配器（真实 HTTP 进程）→ 学习服务内部模型路由接口（假供应商）→ 平台库。

一道合成道法主观题逐点判分并写入平台：带 evidence_ids、confidence（主观维 ≤ 0.5）、真实 model_version、human_review_required。
适配器只知道路由接口的地址与令牌，不持有任何供应商信息。路由接口用 uvicorn 在本机回环上单独起一个实例（不进 lifespan），
模型路由注入 test 环境的假供应商，按作答原文返回预设的逐点判定 JSON。没有 node 或没装知行依赖时跳过（LEARNING_TEST_STRICT=1 时失败）。
全部合成数据（规则 8）。
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path

import httpx
import pytest
import uvicorn

from conftest import CREDENTIAL, counts
from conftest import settings as make_settings
from learning.app import create_app
from learning.config import AdapterTarget
from review_loop import close_loop, console
from learning.model_router import DEFAULT_PATH, FakeProvider, MemorySink, ModelRouter, ProviderReply, Usage, load

ZX = Path(__file__).resolve().parents[3] / "adapters/zhixing"
ZX_ADAPTER = "synthetic-learning-to-zhixing-credential-01"
ZX_ROUTE = "synthetic-zhixing-to-learning-route-token-1"
ANSWER = "居民参与民主决策，体现了居民自治。"  # 合成作答：p1 核心术语、p2 可接受表述、p3 没有
CV = {"region": "nanjing", "exam_year": 2027, "grade": 9, "textbook_version": "bubian-2024", "subject": "zhixing", "content_version": "0.1.0"}


def _missing() -> str | None:
    if not shutil.which("node"):
        return "没有 node"
    if not (ZX / "node_modules/ajv").exists():
        return "没有安装知行适配器依赖（cd adapters/zhixing && npm ci）"
    return None


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _span(text: str) -> tuple[int, int]:
    i = ANSWER.find(text)
    assert i >= 0
    return i, i + len(text)


def scripted_points() -> str:
    """假供应商的“模型输出”：只在作答原文里指证据位置，不含自由文本"""
    p1, p2 = _span("参与民主决策"), _span("居民自治")
    return json.dumps({"points": [
        {"point_id": "p1", "judgment": "full", "score_awarded": 2, "evidence_start": p1[0], "evidence_end": p1[1], "attribution": "none"},
        {"point_id": "p2", "judgment": "partial", "score_awarded": 1, "evidence_start": p2[0], "evidence_end": p2[1], "attribution": "zhuanhua_cuo"},
        {"point_id": "p3", "judgment": "miss", "score_awarded": 0, "evidence_start": None, "evidence_end": None, "attribution": "shenti_cuo"},
    ]}, ensure_ascii=False)


class _Server(uvicorn.Server):
    def install_signal_handlers(self) -> None:  # 在线程里跑
        pass


@pytest.fixture(scope="module")
def route_endpoint():
    """学习服务内部路由接口：单独实例，只用它的 /internal/v1/model/route；模型路由是 test 环境的假供应商"""
    cfg = load(DEFAULT_PATH, "test")
    seen: list[dict] = []

    async def grader(target, messages, key):
        seen.append({"api_name": target.api_name, "user": messages[-1]["content"]})
        return ProviderReply(output=scripted_points(), usage=Usage(100, 50), reported_model=target.api_name)

    router = ModelRouter(cfg, {v: FakeProvider(cfg.vendors[v], grader) for v in cfg.vendors}, sink=MemorySink())
    app = create_app(make_settings(route_clients={"zhixing": ZX_ROUTE}), model_router=router)
    port = _port()
    server = _Server(uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off", log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    deadline = time.time() + 10
    while not server.started:
        if time.time() > deadline:
            pytest.fail("路由接口没有启动")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", seen
    server.should_exit = True
    t.join(timeout=10)


@pytest.fixture(scope="module")
def zhixing_adapter(pg, route_endpoint):
    why = _missing()
    if why:
        if os.environ.get("LEARNING_TEST_STRICT") == "1":
            pytest.fail(why)
        pytest.skip(why)
    sql = subprocess.run(["node", "--no-warnings", "../../tools/content-register/zhixing.ts"], cwd=ZX, check=True, capture_output=True).stdout
    pg.psql(sql.decode())
    port = _port()
    env = {"PATH": os.environ["PATH"], "ZHIXING_ADAPTER_SERVICE_TOKEN": ZX_ADAPTER, "PORT": str(port),
           "ZHIXING_ROUTE_URL": route_endpoint[0], "ZHIXING_ROUTE_TOKEN": ZX_ROUTE,
           "ZHIXING_CONTRACT_VERSION": "1.0.0"}  # 1.0.0 已签字：适配器不需候选版开关
    proc = subprocess.Popen(["node", "--no-warnings", "src/main.ts"], cwd=ZX, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 20
    while True:
        try:
            ok = httpx.get(f"http://127.0.0.1:{port}/plugin/v0/capabilities", headers={"authorization": f"Bearer {ZX_ADAPTER}"}).status_code == 200
        except httpx.TransportError:
            ok = False
        if ok:
            break
        if time.time() > deadline:
            proc.kill()
            pytest.fail("知行适配器没有启动")
        time.sleep(0.2)
    yield f"http://127.0.0.1:{port}", env
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture(autouse=True)
def _no_candidate_switch(monkeypatch):
    # 插件契约 1.0.0、learning-internal v1 都已签字（LT-04 删除了 LEARNING_INTERNAL_V1）：候选版开关关着；计量记录由 conftest 代建
    monkeypatch.delenv("STARBUDDY_ALLOW_CANDIDATE_CONTRACT", raising=False)


@pytest.fixture
async def e2e_api(pools, zhixing_adapter):
    app_pool, _ = pools
    app = create_app(make_settings(adapters={"zhixing": AdapterTarget(zhixing_adapter[0], ZX_ADAPTER)}), pool=app_pool)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as c:
            c.headers["authorization"] = f"Bearer {CREDENTIAL}"
            yield c


def zx_grade(student: str) -> dict:
    return {"request": {
        "context": {"request_id": str(uuid.uuid4()), "contract_version": "1.0.0", "subject": "zhixing", "student_ref": student,
                    "data_class": "L2", "usage_id": str(uuid.uuid4()), "idempotency_key": str(uuid.uuid4()), "content_version": dict(CV)},
        "attempt": {"attempt_id": str(uuid.uuid4()), "item_ref": "zhixing:synthetic-m3-01", "occurred_at": "2026-10-08T19:30:00+08:00",
                    "response": {"text": ANSWER}, "elapsed_sec": 300, "hint_level_used": 0},
        "rubric_version": "zhixing-synthetic@0.1.0"}, "ingest": {"channel": "keypad"}}


async def test_zhixing_subjective_item_graded_point_by_point_and_written(e2e_api, pools, student, route_endpoint, pg):
    _, admin = pools
    r = await e2e_api.post("/internal/v1/grade", json=zx_grade(student))
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["subject"] == "zhixing" and s["human_review_required"] is True and s["semantics"] == "practice"
    assert s["evidence_ids"] and s["verdict"] in ("partial", "needs_review")
    n = await counts(admin, student)
    assert n["grade_result"] == 1 and n["grade_dimension"] >= 1 and n["evidence_event"] >= 1
    async with admin.acquire() as c:
        g = await c.fetchrow("SELECT model_version::text, rubric_version::text, human_review_required FROM platform.grade_result WHERE id = $1::uuid",
                             s["grade_result_id"])
        assert g["model_version"] == "model:fake-a/fake-reasoner@2026-09-01", "真实快照名来自模型路由，不是 routed"
        assert g["rubric_version"] == "zhixing-synthetic@0.1.0" and g["human_review_required"]
        dims = await c.fetch("SELECT judged_by::text, subjective, confidence FROM platform.grade_dimension WHERE grade_result_id = $1::uuid",
                             s["grade_result_id"])
        assert dims and all(d["judged_by"] == "rubric_model" and d["subjective"] and d["confidence"] <= 0.5 for d in dims)
        ev = await c.fetch("SELECT evidence_id::text FROM platform.evidence_event WHERE student_id = $1::uuid", student)
        assert {e["evidence_id"] for e in ev} == set(s["evidence_ids"])
    # 模型调用确实经学习服务的路由接口（T2 判分 Prompt 里带着作答原文），适配器没有别的出口
    assert route_endpoint[1] and ANSWER in route_endpoint[1][-1]["user"]
    # 统一复核队列闭环（冲刺 2-G）：主观维一律进队，staff 经审核台领取、结案；队列与详情不带作答原文
    async with console(pg) as client:
        done = await close_loop(client, source_id=s["grade_result_id"], student_id=student, subject="zhixing",
                                expect_reasons={"subjective_dimension"}, code="regrade_required")
        assert ANSWER not in str(done)


def test_adapter_env_has_no_vendor_config(zhixing_adapter):
    env = zhixing_adapter[1]
    assert not any(k.startswith("MODEL_") or "API_KEY" in k for k in env), "适配器进程不持有任何供应商地址或 Key"
