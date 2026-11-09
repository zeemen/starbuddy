"""联调栈：一次性 Postgres 上的学习服务（进程内，带合成掌握配置）+ 三科适配器（真实 HTTP 子进程）+ 学习服务内部模型路由接口（本机回环）。

做法沿用 services/learning/tests/test_e2e_{aya,zhixing,wenli}.py，只是三科同时启动、共用一个学习服务实例：
- 适配器进程只拿到自己的服务令牌、端口、路由接口地址与令牌；不拿任何数据库连接串或供应商配置（规则 1、ADR 04），
  断言里会核对这一点；
- 学习服务到适配器的 HTTP 走 RecordingTransport：原样转发，只把 grade / diagnose 的响应体留在内存里，
  供“grade 结果溯源字段齐全”断言在契约边界上检查（不落盘，不写日志）；
- 模型路由是 test 环境的假供应商（scripted.ScriptedModel），只接受本机回环。
只连本机临时实例（规则 9）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import asyncpg
import httpx
import uvicorn

from learning.app import create_app
from learning.config import AdapterTarget, Settings
from learning.mastery.recompute import MasteryEngine
from learning.model_router import DEFAULT_PATH, FakeProvider, MemorySink, ModelRouter, ProviderReply, Usage, load
from learning.service import IngestService
from pgharness import LOGIN_ROLE

from . import mastery as mastery_setup
from .scripted import ScriptedModel

ROOT = Path(__file__).resolve().parents[3]
ADAPTERS = ROOT / "adapters"
METER_PROVISION_SQL = ROOT / "services/learning/tests/fixtures/meter_provision.sql"
SUBJECTS = ("aya", "wenli", "zhixing")
LEARNING_CREDENTIAL = "synthetic-bff-to-learning-cohort-credential-01"
LEARNING_SESSION_CREDENTIAL = "synthetic-bff-to-learning-cohort-session-cred-1"  # PLT-24：诊断、再练、复测、今日编排
ADAPTER_TOKENS = {s: f"synthetic-learning-to-{s}-cohort-credential-0001" for s in SUBJECTS}
ROUTE_TOKENS = {s: f"synthetic-{s}-to-learning-cohort-route-token-0001" for s in ("wenli", "zhixing")}
ENV_PREFIX = {"aya": "AYA", "wenli": "WENLI", "zhixing": "ZHIXING"}


def missing_prerequisites() -> str | None:
    if not shutil.which("node"):
        return "没有 node"
    for s in SUBJECTS:
        if not (ADAPTERS / s / "node_modules/ajv").exists():
            return f"没有安装 {s} 适配器依赖（cd adapters/{s} && npm ci）"
    return None


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def register_content(pg) -> None:
    """各科内容登记：适配器脚本生成的 SQL（只增不改）。psql 以管理员连接，先 SET ROLE starbuddy_learning，
    整段 SQL 都以学习服务角色执行（脚本自身的 SET LOCAL ROLE 也是同一角色），不借管理员权限写入。"""
    for s in SUBJECTS:
        sql = subprocess.run(["node", "--no-warnings", "scripts/register-content.ts"], cwd=ADAPTERS / s,
                             check=True, capture_output=True).stdout
        pg.psql("SET ROLE starbuddy_learning;\n" + sql.decode())


def publish_content(pg) -> None:
    """4-G 迁移 0039 起内容版本只能经签字函数发布（BFF 才看得到能力视图，星图截图要用）：在这个一次性库里把三科登记的草稿版本
    以合成 staff 签字发布。发布函数见 db/seeds/dev/syn-publish-content.sql（同一 psql 会话里先定义再调用）；
    数学在合成掌握配置绑定合成地图之后发布（指向已发布的合成地图），道法没有地图时建确定 ID 的占位地图、发布后即撤下。"""
    helper = (ROOT / "db/seeds/dev/syn-publish-content.sql").read_text(encoding="utf-8")
    pg.psql(helper + "\nSELECT pg_temp.syn_publish_content(v.id) FROM platform.curriculum_version v"
                     " WHERE v.status = 'draft' ORDER BY v.subject, v.id;")


DRIVER_PACKAGES = ("pg", "postgres", "pg-promise", "pg-pool", "knex", "sequelize", "typeorm", "prisma", "@prisma/client",
                   "kysely", "drizzle-orm", "slonik", "mysql", "mysql2", "sqlite3", "better-sqlite3", "@neondatabase/serverless")


def adapter_db_drivers() -> dict[str, list[str]]:
    """三个适配器 package.json（dependencies 与 devDependencies）里的数据库驱动；应当都为空"""
    out = {}
    for s in SUBJECTS:
        pkg = json.loads((ADAPTERS / s / "package.json").read_text(encoding="utf-8"))
        deps = set(pkg.get("dependencies", {})) | set(pkg.get("devDependencies", {}))
        out[s] = sorted(d for d in deps if d in DRIVER_PACKAGES)
    return out


class ConnectionLog:
    """一次性库的连接日志：开启 log_connections（只对这个临时实例），联调结束后读出期间连过库的登录角色。"""

    def __init__(self, pg) -> None:
        self.path = Path(pg.work) / "pg.log"
        pg.psql("ALTER SYSTEM SET log_connections = on; SELECT pg_reload_conf();")
        time.sleep(0.3)
        self.offset = self.path.stat().st_size if self.path.exists() else 0

    def users(self) -> set[str]:
        with open(self.path, "rb") as f:
            f.seek(self.offset)
            text = f.read().decode("utf-8", "replace")
        return set(re.findall(r"connection authorized: user=(\S+)", text))


class _Server(uvicorn.Server):
    def install_signal_handlers(self) -> None:  # 在线程里跑
        pass


@contextmanager
def route_endpoint(model: ScriptedModel):
    cfg = load(DEFAULT_PATH, "test")

    async def grader(target, messages, key):
        return ProviderReply(output=model.reply(messages[-1]["content"]), usage=Usage(100, 50), reported_model=target.api_name)

    router = ModelRouter(cfg, {v: FakeProvider(cfg.vendors[v], grader) for v in cfg.vendors}, sink=MemorySink())
    app = create_app(Settings(env="test", database_url="unused", service_credential=LEARNING_CREDENTIAL,
                              route_clients=dict(ROUTE_TOKENS)), model_router=router)
    port = _port()
    server = _Server(uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off", log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    deadline = time.time() + 10
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("路由接口没有启动")
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        t.join(timeout=10)


@dataclass
class AdapterProc:
    subject: str
    url: str
    env: dict
    proc: subprocess.Popen
    capabilities: dict = field(default_factory=dict)


def _adapter_env(subject: str, port: int, route_url: str) -> dict:
    p = ENV_PREFIX[subject]
    # v1.0 签字后（specs/contract-v1.0-signoff）三科能力声明缺省都报 1.0.0，联调请求也用 1.0.0：不设版本变量、不开候选版开关，
    # 联调本身就验证“缺省按 1.0.0 工作”；运行元数据里的能力声明版本一致
    env = {"PATH": os.environ["PATH"], f"{p}_ADAPTER_SERVICE_TOKEN": ADAPTER_TOKENS[subject], "PORT": str(port)}
    if subject in ROUTE_TOKENS:
        env.update({f"{p}_ROUTE_URL": route_url, f"{p}_ROUTE_TOKEN": ROUTE_TOKENS[subject]})
    return env


@contextmanager
def adapters(route_url: str):
    procs: dict[str, AdapterProc] = {}
    try:
        for s in SUBJECTS:
            port = _port()
            env = _adapter_env(s, port, route_url)
            proc = subprocess.Popen(["node", "--no-warnings", "src/main.ts"], cwd=ADAPTERS / s, env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            procs[s] = AdapterProc(s, f"http://127.0.0.1:{port}", env, proc)
        for s, a in procs.items():
            deadline = time.time() + 30
            while True:
                try:
                    r = httpx.get(f"{a.url}/plugin/v0/capabilities", headers={"authorization": f"Bearer {ADAPTER_TOKENS[s]}"})
                    if r.status_code == 200:
                        a.capabilities = r.json()
                        break
                except httpx.TransportError:
                    pass
                if time.time() > deadline or a.proc.poll() is not None:
                    raise RuntimeError(f"{s} 适配器没有启动")
                time.sleep(0.2)
        yield procs
    finally:
        for a in procs.values():
            a.proc.terminate()
        for a in procs.values():
            try:
                a.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                a.proc.kill()


class RecordingTransport(httpx.AsyncBaseTransport):
    """学习服务 → 适配器：原样转发；grade / diagnose 的响应体只留在内存里供断言检查。"""

    def __init__(self) -> None:
        self.inner = httpx.AsyncHTTPTransport()
        self.records: list[dict] = []
        self.calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        resp = await self.inner.handle_async_request(request)
        method = request.url.path.rsplit("/", 1)[-1]
        if method in ("grade", "diagnose"):
            body = await resp.aread()
            try:
                parsed = json.loads(body)
            except ValueError:
                parsed = None
            self.records.append({"method": method, "port": request.url.port, "status": resp.status_code, "body": parsed})
            return httpx.Response(resp.status_code, headers=resp.headers, content=body, request=request)
        return resp

    async def aclose(self) -> None:
        await self.inner.aclose()


@dataclass
class Stack:
    api: httpx.AsyncClient
    admin: asyncpg.Pool
    app_pool: asyncpg.Pool
    transport: RecordingTransport
    adapters: dict[str, AdapterProc]
    model: ScriptedModel
    connlog: ConnectionLog
    mastery: MasteryEngine
    # 进程内的写入服务（与学习服务里复测模块用的是同一个 IngestService 类）：复测作答的用途只由可信调用方按票据写入，
    # 直接经 HTTP 只能是 practice（LT-04 决定记录（续）第 3 条），所以联调的复测作答经它写（计量按题库自练计次核对）
    ingest: IngestService


@asynccontextmanager
async def running_stack(pg, model: ScriptedModel, *, register: bool = True):
    """pg：pgharness.EphemeralPG（已执行角色与全部迁移）。"""
    # 插件契约 1.0.0（2026-10-09）与 learning-internal v1（2026-10-20，LT-04 删除了 LEARNING_INTERNAL_V1）都已签字：
    # 联调期间显式关掉候选版开关，证明插件侧缺省不靠它；结束后恢复（不影响同一 pytest 会话里的其他测试）
    previous = {k: os.environ.get(k) for k in ("STARBUDDY_ALLOW_CANDIDATE_CONTRACT",)}
    os.environ.pop("STARBUDDY_ALLOW_CANDIDATE_CONTRACT", None)
    try:
        async with _stack(pg, model, register=register) as st:
            yield st
    finally:
        for k, v in previous.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@asynccontextmanager
async def _stack(pg, model: ScriptedModel, *, register: bool):
    connlog = ConnectionLog(pg)
    if register:
        register_content(pg)
    # 合成掌握配置（KGE-04 第二步 PR 3）：合成数学地图、打标与内容版本绑定；复测由 platform.retest_task 驱动（mastery.py）
    pg.psql(mastery_setup.seed_sql())
    # 计量夹具（LT-04）：学习服务写入前核对计量；联调不经 BFF，由测试库里的 learningtest.provision_meter 代建预留与自练计次（replay.py）
    pg.psql(METER_PROVISION_SQL.read_text(encoding="utf-8"))
    if register:
        publish_content(pg)
    app_pool = await asyncpg.create_pool(**pg.dsn(LOGIN_ROLE), min_size=1, max_size=20)
    admin = await asyncpg.create_pool(**pg.dsn("postgres"), min_size=1, max_size=4)
    try:
        with route_endpoint(model) as route_url, adapters(route_url) as procs:
            transport = RecordingTransport()
            settings = Settings(env="test", database_url="unused", service_credential=LEARNING_CREDENTIAL,
                                adapters={s: AdapterTarget(p.url, ADAPTER_TOKENS[s]) for s, p in procs.items()})
            engine = MasteryEngine(app_pool, {"aya": mastery_setup.config()})
            app = create_app(settings, pool=app_pool, transport=transport, mastery=engine)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test",
                                             timeout=60) as client, \
                        httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False) as adapter_client:
                    client.headers["authorization"] = f"Bearer {LEARNING_CREDENTIAL}"
                    ingest = IngestService(settings, app_pool, adapter_client, mastery=engine)
                    yield Stack(client, admin, app_pool, transport, procs, model, connlog, engine, ingest)
    finally:
        await app_pool.close()
        await admin.close()
        pg.psql(mastery_setup.retire_sql())
