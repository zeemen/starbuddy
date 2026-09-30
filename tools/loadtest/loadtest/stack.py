"""本机一次性压测栈（spec 设计要点 1“目标环境”）。只连本机、只用合成数据（规则 8、9）；预发压测见 RUNBOOK.md。

组成（全部监听 127.0.0.1，结束即删）：
  一次性 Postgres（services/learning/tests/pgharness.EphemeralPG：全部迁移，Unix socket，不监听 TCP）
    + pg_stat_statements（只在临时实例）+ 计量时钟替换 + 已发布的 loadtest-plan@1（seed.py）；
  假模型供应商（fake_provider.py，独立进程，OpenAI 兼容协议，可注入故障）；
  三科适配器（复用 tools/e2e-cohort 的 stack.adapters，真实 node 进程，只拿到学习服务路由接口的地址与令牌）；
  学习服务（独立进程 python -m learning，LEARNING_ENV=test：模型路由按仓库路由表的 test 节经 OpenAI 兼容适配器调假供应商）；
  BFF（独立进程 node src/main.ts，STARBUDDY_ENV=test，短信捕获桩写验证码到临时文件；限流阈值放宽的一份配置同 apps/web/scripts/e2e.sh）。
服务间令牌沿用 e2e_cohort 的合成常量（不是真实凭据）；日志只落在临时目录，压测后只取调用记录的元数据（token 数、任务、科目）。
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from contextlib import ExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path

import asyncpg
import httpx

from e2e_cohort.stack import ADAPTER_TOKENS, LEARNING_CREDENTIAL, ROUTE_TOKENS, adapters, register_content
from pgharness import LOGIN_ROLE, EphemeralPG

from . import dbstats, harness, seed

ROOT = Path(__file__).resolve().parents[3]
TOOLS = ROOT / "tools/loadtest"

PY = ROOT / "services/learning/.venv/bin/python"


def missing_prerequisites() -> str | None:
    from e2e_cohort.stack import missing_prerequisites as base

    why = base()
    if why:
        return why
    if not PY.exists():
        return "没有 services/learning/.venv"
    if not (ROOT / "services/bff/node_modules/pg").exists():
        return "没有安装 BFF 依赖（cd services/bff && npm ci）"
    return None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_port(port: int, proc: subprocess.Popen, what: str, log: Path, timeout: float = 40.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.2)
    tail = log.read_text(encoding="utf-8", errors="replace")[-2000:] if log.exists() else ""
    raise RuntimeError(f"{what} 没有启动（最后日志：{tail}）")


@contextmanager
def process(cmd: list[str], cwd: Path, env: dict, log: Path, port: int, what: str):
    with open(log, "wb") as out:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    try:
        wait_port(port, proc, what, log)
        yield proc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@dataclass
class Stack:
    pg: EphemeralPG
    work: Path
    bff_url: str
    learning_url: str
    provider_url: str
    shell_origin: str
    sms_inbox: Path
    learning_log: Path
    admin: asyncpg.Pool
    bff_pool: asyncpg.Pool
    scheduler: asyncpg.Pool
    stat_statements: bool
    harness_url: str

    async def set_fault(self, vendor: str, spec: dict | None) -> None:
        body = {"clear": True, "vendor": vendor} if spec is None else {"vendor": vendor, "spec": spec}
        async with httpx.AsyncClient(trust_env=False) as c:
            r = await c.post(f"{self.provider_url}/_control/faults", json=body)
            r.raise_for_status()

    async def provider_stats(self) -> dict:
        async with httpx.AsyncClient(trust_env=False) as c:
            return (await c.get(f"{self.provider_url}/_control/stats")).json()


def relaxed_auth_config(dst: Path) -> None:
    """同 apps/web/scripts/e2e.sh：阈值 ×1000 的一份配置，只在 STARBUDDY_ENV=test 时 BFF 才认"""
    c = json.loads((ROOT / "services/bff/config/auth.json").read_text(encoding="utf-8"))
    for v in c["rate_limits"].values():
        v["limit"] = min(v["limit"] * 1000, 1_000_000)
    dst.write_text(json.dumps(c), encoding="utf-8")


@asynccontextmanager
async def local_stack(*, seed_value: int, students: int, provider_median_ms: float, provider_p90_ms: float,
                      retry_scale: float = 1.0):
    """students：假供应商预先登记合成作答的学生数（学生旅程的合成对象数）"""
    work = Path(tempfile.mkdtemp(prefix="starbuddy-loadtest."))
    os.chmod(work, 0o700)
    pg = EphemeralPG()
    pg.start()
    previous = os.environ.get("STARBUDDY_ALLOW_CANDIDATE_CONTRACT")
    os.environ["STARBUDDY_ALLOW_CANDIDATE_CONTRACT"] = "1"  # 插件契约 1.0.0 仍是候选版：只在这个一次性栈放开（同 e2e_cohort）
    try:
        stat = dbstats.enable_stat_statements(pg)
        pg.psql(seed.setup_sql())
        register_content(pg)
        with ExitStack() as es:
            env_base = {"PATH": os.environ["PATH"], "LANG": "C.UTF-8"}
            # ---- 假供应商
            pport = free_port()
            provider_url = f"http://127.0.0.1:{pport}"
            es.enter_context(process(
                [str(PY), "-m", "loadtest.fake_provider", "--port", str(pport), "--seed", str(seed_value),
                 "--students", str(students), "--median-ms", str(provider_median_ms), "--p90-ms", str(provider_p90_ms)],
                TOOLS, env_base | {"PYTHONPATH": f"{TOOLS}:{ROOT / 'tools/e2e-cohort'}"}, work / "provider.log", pport, "假供应商"))
            # ---- 适配器（路由接口地址 = 学习服务，先定端口）
            lport = free_port()
            learning_url = f"http://127.0.0.1:{lport}"
            procs = es.enter_context(adapters(learning_url))
            # ---- 学习服务
            db_url = f"postgresql://{LOGIN_ROLE}@/{pg.db}?host={pg.work}"
            lenv = env_base | {
                "LEARNING_ENV": "test", "PORT": str(lport), "LEARNING_DATABASE_URL": db_url, "LEARNING_SERVICE_TOKEN": LEARNING_CREDENTIAL,
                "LEARNING_DIAGNOSIS_PRESENTER": "synthetic", "STARBUDDY_ALLOW_CANDIDATE_CONTRACT": "1",
                "MODEL_FAKE_A_URL": f"{provider_url}/fake-a", "MODEL_FAKE_B_URL": f"{provider_url}/fake-b",
                "MODEL_FAKE_X_URL": f"{provider_url}/fake-x",
            }
            for s, p in procs.items():
                lenv[f"LEARNING_ADAPTER_{s.upper()}_URL"] = p.url
                lenv[f"LEARNING_ADAPTER_{s.upper()}_TOKEN"] = ADAPTER_TOKENS[s]
            for s, tok in ROUTE_TOKENS.items():
                lenv[f"LEARNING_ROUTE_CLIENT_{s.upper()}_TOKEN"] = tok
            learning_log = work / "learning.log"
            es.enter_context(process([str(PY), "-m", "learning"], ROOT / "services/learning", lenv, learning_log, lport, "学习服务"))
            # ---- BFF
            bport, wport = free_port(), free_port()
            shell_origin = f"http://127.0.0.1:{wport}"
            sms = work / "sms-inbox.jsonl"
            sms.touch(mode=0o600)
            auth_cfg = work / "auth.relaxed.json"
            relaxed_auth_config(auth_cfg)
            benv = env_base | {
                "BFF_DATABASE_URL": f"postgresql://bff_app@/{pg.db}?host={pg.work}", "PORT": str(bport), "BFF_AUTH_CONFIG": str(auth_cfg),
                "BFF_LEARNING_URL": learning_url, "BFF_LEARNING_TOKEN": LEARNING_CREDENTIAL, "STARBUDDY_ENV": "test",
                "BFF_SHELL_ORIGIN": shell_origin, "BFF_SMS_CAPTURE_FILE": str(sms),
            }
            es.enter_context(process(["node", "--no-warnings", "src/main.ts"], ROOT / "services/bff", benv, work / "bff.log", bport, "BFF"))
            # ---- 计量驱动桩：批改经 BFF 的 meteredCall（契约审查 H1）
            harness_url = es.enter_context(harness.meter_harness(
                f"postgresql://bff_app@/{pg.db}?host={pg.work}", learning_url, LEARNING_CREDENTIAL,
                lambda: int(pg.psql(seed.METER_NOW_MS_SQL).strip()), retry_scale=retry_scale,
                log=work / "harness.log"))
            admin = await asyncpg.create_pool(**pg.dsn("postgres"), min_size=1, max_size=4)
            bff_pool = await asyncpg.create_pool(**pg.dsn("bff_app"), min_size=2, max_size=20)
            scheduler = await asyncpg.create_pool(**pg.dsn("loadtest_scheduler"), min_size=1, max_size=1)
            try:
                yield Stack(pg, work, f"http://127.0.0.1:{bport}", learning_url, provider_url, shell_origin, sms, learning_log,
                            admin, bff_pool, scheduler, stat, harness_url)
            finally:
                await admin.close()
                await bff_pool.close()
                await scheduler.close()
    finally:
        if previous is None:
            os.environ.pop("STARBUDDY_ALLOW_CANDIDATE_CONTRACT", None)
        else:
            os.environ["STARBUDDY_ALLOW_CANDIDATE_CONTRACT"] = previous
        pg.stop()
        shutil.rmtree(work, ignore_errors=True)


def call_records(learning_log: Path) -> list[dict]:
    """学习服务日志里的模型调用记录（ledger.LogSink 的 model_call 事件）→ 只留成本模型需要的元数据：
    科目、任务、数据级别、结果、token 数、耗时、尝试次数。不留 model_version、供应商报告的模型名、usage / 幂等键摘要。"""
    out = []
    if not learning_log.exists():
        return out
    for line in learning_log.read_text(encoding="utf-8", errors="replace").splitlines():
        if '"model_call"' not in line:
            continue
        try:
            d = json.loads(line[line.index("{"):])
        except ValueError:
            continue
        if d.get("event") != "model_call":
            continue
        out.append({"subject": d.get("subject"), "task": d.get("task"), "data_class": d.get("data_class"), "outcome": d.get("outcome"),
                    "input_tokens": int(d.get("input_tokens") or 0), "output_tokens": int(d.get("output_tokens") or 0),
                    "elapsed_ms": int(d.get("elapsed_ms") or 0), "attempts": len(d.get("attempts") or [])})
    return out
