"""端到端（ADP-语文-01 验收）：学习服务 → 文砺适配器（真实 HTTP 进程）→ 学习服务内部模型路由接口（假供应商）→ 平台库。

一篇合成作文五维结果写入平台：带 evidence_ids、四个主观维 confidence ≤ 0.5、字数规范维 rubric_rule、卷面书写维不可判、
真实 model_version、human_review_required；再走一遍统一复核队列闭环（冲刺 2-G 三科之一）。
适配器只知道路由接口的地址与令牌，不持有任何供应商信息。路由接口用 uvicorn 在本机回环上单独起一个实例（不进 lifespan），
模型路由注入 test 环境的假供应商，返回预设的逐维定档 JSON。没有 node 或没装文砺依赖时跳过（LEARNING_TEST_STRICT=1 时失败）。
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

WL = Path(__file__).resolve().parents[3] / "adapters/wenli"
WL_ADAPTER = "synthetic-learning-to-wenli-credential-0001"
WL_ROUTE = "synthetic-wenli-to-learning-route-token-001"
ESSAY = ("（合成作文）那一次，我读懂了坚持。清晨的操场上，我一圈一圈地跑，汗水模糊了视线。"
         "起初我想放弃，后来想起老师的话：慢慢来，比较快。于是我咬牙跑完了最后一圈。那一刻我明白了，坚持不是不累，而是累了还往前走。")
CV = {"region": "nanjing", "exam_year": 2027, "grade": 9, "textbook_version": "bubian-2024", "subject": "wenli", "content_version": "0.1.0"}


def _missing() -> str | None:
    if not shutil.which("node"):
        return "没有 node"
    if not (WL / "node_modules/ajv").exists():
        return "没有安装文砺适配器依赖（cd adapters/wenli && npm ci）"
    return None


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _span(text: str) -> tuple[int, int]:
    i = ESSAY.find(text)
    assert i >= 0
    return i, i + len(text)


def scripted_levels() -> str:
    """假供应商的“模型输出”：每维只给档位、证据在作文原文里的位置与不确定区间，不含自由文本"""
    spans = {"shenti_liyi": "读懂了坚持", "neirong_cailiao": "一圈一圈地跑", "jiegou_cengci": "起初我想放弃", "yuyan_biaoda": "慢慢来，比较快"}
    levels = {"shenti_liyi": ("L3", "L2", "L3"), "neirong_cailiao": ("L2", "L2", "L3"), "jiegou_cengci": ("L3", "L3", "L3"),
              "yuyan_biaoda": ("L2", "L1", "L2")}
    dims = []
    for d, frag in spans.items():
        st, en = _span(frag)
        lv, lo, hi = levels[d]
        dims.append({"dimension_id": d, "level": lv, "evidence_start": st, "evidence_end": en, "uncertainty_levels": {"low": lo, "high": hi}})
    return json.dumps({"dimensions": dims}, ensure_ascii=False)


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
        return ProviderReply(output=scripted_levels(), usage=Usage(100, 50), reported_model=target.api_name)

    router = ModelRouter(cfg, {v: FakeProvider(cfg.vendors[v], grader) for v in cfg.vendors}, sink=MemorySink())
    app = create_app(make_settings(route_clients={"wenli": WL_ROUTE}), model_router=router)
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
def wenli_adapter(pg, route_endpoint):
    why = _missing()
    if why:
        if os.environ.get("LEARNING_TEST_STRICT") == "1":
            pytest.fail(why)
        pytest.skip(why)
    sql = subprocess.run(["node", "--no-warnings", "../../tools/content-register/wenli.ts"], cwd=WL, check=True, capture_output=True).stdout
    pg.psql(sql.decode())
    port = _port()
    env = {"PATH": os.environ["PATH"], "WENLI_ADAPTER_SERVICE_TOKEN": WL_ADAPTER, "PORT": str(port),
           "WENLI_ROUTE_URL": route_endpoint[0], "WENLI_ROUTE_TOKEN": WL_ROUTE,
           "WENLI_CONTRACT_VERSION": "1.0.0"}  # 1.0.0 已签字：适配器不需候选版开关
    proc = subprocess.Popen(["node", "--no-warnings", "src/main.ts"], cwd=WL, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 20
    while True:
        try:
            ok = httpx.get(f"http://127.0.0.1:{port}/plugin/v0/capabilities", headers={"authorization": f"Bearer {WL_ADAPTER}"}).status_code == 200
        except httpx.TransportError:
            ok = False
        if ok:
            break
        if time.time() > deadline:
            proc.kill()
            pytest.fail("文砺适配器没有启动")
        time.sleep(0.2)
    yield f"http://127.0.0.1:{port}", env
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture(autouse=True)
def _no_candidate_switch(monkeypatch):
    # 插件契约 1.0.0、learning-internal v1 都已签字（LT-04 删除了 LEARNING_INTERNAL_V1）：候选版开关关着；计量记录由 conftest 代建
    monkeypatch.delenv("STARBUDDY_ALLOW_CANDIDATE_CONTRACT", raising=False)


@pytest.fixture
async def e2e_api(pools, wenli_adapter):
    app_pool, _ = pools
    app = create_app(make_settings(adapters={"wenli": AdapterTarget(wenli_adapter[0], WL_ADAPTER)}), pool=app_pool)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as c:
            c.headers["authorization"] = f"Bearer {CREDENTIAL}"
            yield c


def wl_grade(student: str) -> dict:
    return {"request": {
        "context": {"request_id": str(uuid.uuid4()), "contract_version": "1.0.0", "subject": "wenli", "student_ref": student,
                    "data_class": "L2", "usage_id": str(uuid.uuid4()), "idempotency_key": str(uuid.uuid4()), "content_version": dict(CV)},
        "attempt": {"attempt_id": str(uuid.uuid4()), "item_ref": "wenli:synthetic-essay-01", "occurred_at": "2026-10-08T19:30:00+08:00",
                    "response": {"text": ESSAY}, "elapsed_sec": 2400, "hint_level_used": 0},
        "rubric_version": "wenli-essay-5d@0.1.0"}, "ingest": {"channel": "keypad"}}


SUBJECTIVE = {"shenti_liyi", "neirong_cailiao", "jiegou_cengci", "yuyan_biaoda"}


async def test_wenli_essay_five_dimensions_written_and_reviewed(e2e_api, pools, student, route_endpoint, pg):
    _, admin = pools
    r = await e2e_api.post("/internal/v1/grade", json=wl_grade(student))
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["subject"] == "wenli" and s["human_review_required"] is True and s["evidence_ids"]
    async with admin.acquire() as c:
        g = await c.fetchrow("""SELECT model_version::text, rubric_version::text, human_review_required, review_reasons::text[] AS reasons
                                  FROM platform.grade_result WHERE id = $1::uuid""", s["grade_result_id"])
        assert g["model_version"] == "model:fake-a/fake-chat@2026-09-01", "真实快照名来自模型路由（T4 路由），不是 routed"
        assert g["rubric_version"] == "wenli-essay-5d@0.1.0" and g["human_review_required"]
        assert {"subjective_dimension", "dimension_unavailable"} <= set(g["reasons"])
        dims = {d["dimension_id"]: d for d in await c.fetch(
            """SELECT dimension_id, score, judged_by::text, subjective, confidence, rule_ref::text, evidence_ids::text[] AS ev
                 FROM platform.grade_dimension WHERE grade_result_id = $1::uuid""", s["grade_result_id"])}
        assert SUBJECTIVE <= set(dims)
        assert all(dims[d]["judged_by"] == "rubric_model" and dims[d]["subjective"] and dims[d]["confidence"] <= 0.5
                   and dims[d]["score"] is not None for d in SUBJECTIVE)
        assert dims["zishu_guifan"]["judged_by"] == "rubric_rule" and dims["zishu_guifan"]["rule_ref"] == "wenli-convention-rules@0.1.0"
        assert dims["juanmian_shuxie"]["score"] is None, "卷面书写要看图片，路由接口只收文本：不可判"
        assert all(set(d["ev"]) <= set(s["evidence_ids"]) for d in dims.values())
        ev = await c.fetch("SELECT evidence_id::text FROM platform.evidence_event WHERE student_id = $1::uuid", student)
        assert {e["evidence_id"] for e in ev} == set(s["evidence_ids"])
    # 判分 Prompt 经学习服务的路由接口发出（作文原文在最后一条 user 消息里）
    assert route_endpoint[1] and "读懂了坚持" in route_endpoint[1][-1]["user"]
    # 统一复核队列闭环（冲刺 2-G 验收：三科各一条）
    async with console(pg) as client:
        done = await close_loop(client, source_id=s["grade_result_id"], student_id=student, subject="wenli",
                                expect_reasons={"subjective_dimension"})
        assert "读懂了坚持" not in str(done)


def test_adapter_env_has_no_vendor_config(wenli_adapter):
    env = wenli_adapter[1]
    assert not any(k.startswith("MODEL_") or "API_KEY" in k for k in env), "适配器进程不持有任何供应商地址或 Key"
