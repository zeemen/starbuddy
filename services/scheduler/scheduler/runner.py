"""运行器：一个任务跑一轮 = 启动核对 → 取咨询锁 → 子进程（期间定期探活锁连接）→ 结构化日志。

- 环境白名单：子进程的环境只有固定的基础变量（BASE_ENV，PATH 用固定值，不继承调度器的）加上该任务身份的运行环境变量、连接串变量与
  任务声明的附加变量（每个身份的显式白名单：精确名或登记的前缀；连接串类名字只认该身份自己的）；其他一概不传。
- 语句超时：传给子进程的连接串追加 statement_timeout（任务超时的 90%，至少 1 秒）。asyncpg 把连接串里不认识的查询参数当作
  服务端设置，四个子命令都用这一个连接串连库，所以每条语句都受限，不用改子命令。
- 启动核对：身份的运行环境变量只收 dev / test；所有 *_DATABASE_URL 都必须是本机（复用审核台命令行的 local_only）；
  子进程解释器必须存在。不过即 refused（退出码 2，alert）。
- 互斥：按该任务身份的连接取 pg_try_advisory_lock（键由任务名派生），连上后先核对服务端地址（check_server_local）；取不到就跳过本轮
  （skipped_locked）。锁是会话级的，要求直连 Postgres。子进程运行期间每 PROBE_SECONDS 秒对锁连接 SELECT 1 探活：连接丢了（锁随之释放，
  别的实例可能已经开跑）就立刻杀掉子进程组，记 lock_lost（alert）。
- 超时：任务级超时（默认 10 分钟）到了先 SIGTERM 整个进程组，5 秒后 SIGKILL；记 timeout。
- 退出码映射（沿用各命令行的约定）：0 → ok；该身份的“拒绝”退出码（审核台 1、2，其余 2）→ refused；1 → partial；其他（含被信号杀掉）
  → crashed；超时 → timeout；退出码 0 但最后一行不是 JSON 对象 → bad_output。非 0、超时、锁丢失、stale_estimate > 0 一律记 alert。
- 日志一行 JSON：任务名、开始与结束时间、退出码、计数（子进程最后一行 JSON 里的计数，清洗后只留短键与数值、短编码）、
  错误摘要（子进程标准错误末行：去掉连接串与 user=…，UUID 换成 uuid，只留字符白名单）。不含学生 ID、连接串、环境变量的值。
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Mapping, TextIO
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .config import SERVICES, Identity, Job, env_name_ok, matched_name_ok

ALLOWED_ENVS = ("dev", "test")
BASE_ENV: Mapping[str, str] = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8", "LANG": "C.UTF-8"}
KILL_GRACE_SECONDS = 5.0
PROBE_SECONDS = 15.0
PROBE_TIMEOUT = 5.0
MAX_OUTPUT = 1 << 20
EXIT = {"ok": 0, "partial": 1, "refused": 2, "crashed": 1, "timeout": 1, "bad_output": 1, "lock_error": 2, "lock_lost": 1,
        "skipped_locked": 0, "skipped_running": 0, "dry_run": 0}
ALERT = frozenset({"partial", "refused", "crashed", "timeout", "bad_output", "lock_error", "lock_lost", "skipped_running"})
_UUID = re.compile(r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}")
_KEY = re.compile(r"[a-z][a-z0-9_]{0,47}")
_TEXT = re.compile(r"[A-Za-z0-9_.:+-]{1,40}")
_URLISH = re.compile(r"postgres(?:ql)?://\S+", re.I)
_USER = re.compile(r"\buser=\S*", re.I)
_UNSAFE = re.compile(r"[^A-Za-z0-9_ .:()-]")


class PreflightError(Exception):
    """启动核对不过：不连库、不起子进程"""


def lock_key(job_name: str) -> int:
    """咨询锁键：由任务名派生的 64 位有符号整数（同名任务同一把锁，不同任务互不影响）"""
    return int.from_bytes(hashlib.sha256(f"starbuddy.scheduler:{job_name}".encode("utf-8")).digest()[:8], "big", signed=True)


def statement_timeout_ms(timeout_seconds: float) -> int:
    """略小于任务超时：90%，至少 1 秒"""
    return max(1000, int(timeout_seconds * 900))


def with_statement_timeout(url: str, ms: int) -> str:
    """连接串追加（或替换）statement_timeout 查询参数"""
    u = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k != "statement_timeout"]
    q.append(("statement_timeout", str(ms)))
    return urlunsplit((u.scheme, u.netloc, u.path, urlencode(q), u.fragment))


def _local_checks() -> tuple[str, Callable[[str], str], Callable[[Any], Any]]:
    """复用审核台命令行的本机核对（services/learning/review_console/__main__.py；同 insight.jobs 的接法）"""
    learning = str(SERVICES / "learning")
    if learning not in sys.path:
        sys.path.append(learning)
    from review_console.__main__ import NOT_LOCAL, check_server_local, local_only
    return NOT_LOCAL, local_only, check_server_local


def check_local_url(name: str, url: str) -> None:
    _, local_only, _ = _local_checks()
    try:
        local_only(url)
    except SystemExit:
        raise PreflightError(f"{name} 必须是本机数据库的 postgresql:// 连接串（规则 9）") from None


async def check_server_local(conn: Any) -> None:
    _, _, check = _local_checks()
    try:
        await check(conn)
    except SystemExit:
        raise PreflightError("连上的服务端不是本机（规则 9）") from None


def child_env(job: Job, environ: Mapping[str, str]) -> dict[str, str]:
    """子进程环境 = 基础变量 + 该身份的运行环境与连接串 + 任务声明的附加变量（白名单）；缺必填变量即拒"""
    out = dict(BASE_ENV)
    for name in job.required_env:
        if not environ.get(name):
            raise PreflightError(f"任务 {job.name} 缺少 {name}")
        out[name] = environ[name]
    for pattern in job.extra_env:
        if not env_name_ok(job.identity, pattern):  # 配置加载时已核过；直接构造的任务也再核一次
            raise PreflightError(f"任务 {job.name} 的附加变量 {pattern} 不在身份 {job.identity.name} 的白名单里")
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            out.update({k: v for k, v in environ.items() if v and matched_name_ok(job.identity, prefix, k)})
        elif environ.get(pattern):
            out[pattern] = environ[pattern]
    return out


def preflight(job: Job, environ: Mapping[str, str], interpreter: Path) -> dict[str, str]:
    """一个任务的启动核对：返回子进程环境（连接串已追加语句超时）"""
    env = child_env(job, environ)
    if env[job.identity.env_var] not in ALLOWED_ENVS:
        raise PreflightError(f"{job.identity.env_var} 必须是 {ALLOWED_ENVS} 之一（规则 9）")
    for k, v in env.items():
        if k.endswith("_DATABASE_URL"):
            check_local_url(k, v)
    if not interpreter.exists():
        raise PreflightError(f"任务 {job.name} 的子进程解释器不存在（services/{job.identity.service}/.venv）")
    url = job.identity.url_var
    env[url] = with_statement_timeout(env[url], statement_timeout_ms(job.timeout_seconds))
    return env


def sanitize(v: Any, depth: int = 0) -> Any:
    """计数清洗：只留短键与数值、布尔、短编码；像 UUID 的字符串、长文本、深层结构一律丢掉（日志不含学生 ID）"""
    if isinstance(v, bool) or v is None or isinstance(v, int):
        return v
    if isinstance(v, float):
        return v if v == v and abs(v) != float("inf") else None
    if isinstance(v, str):
        return v if _TEXT.fullmatch(v) and not _UUID.search(v) else None
    if depth >= 3:
        return None
    if isinstance(v, dict):
        out = {}
        for k, x in list(v.items())[:64]:
            if isinstance(k, str) and _KEY.fullmatch(k):
                s = sanitize(x, depth + 1)
                if s is not None or x is None:
                    out[k] = s
        return out
    if isinstance(v, list):
        return [s for s in (sanitize(x, depth + 1) for x in v[:32]) if s is not None]
    return None


def parse_counts(stdout: bytes) -> dict[str, Any] | None:
    lines = [ln for ln in stdout.decode("utf-8", "replace").splitlines() if ln.strip()]
    if not lines:
        return None
    try:
        doc = json.loads(lines[-1])
    except ValueError:
        return None
    return sanitize(doc) if isinstance(doc, dict) else None


def stderr_tail(stderr: bytes) -> str:
    """错误摘要：标准错误末行去掉连接串与 user=…，UUID 换成 uuid，只留字符白名单（SQLSTATE 是字母数字，保留）"""
    lines = [ln for ln in stderr.decode("utf-8", "replace").splitlines() if ln.strip()]
    if not lines:
        return ""
    s = _USER.sub(" ", _URLISH.sub(" ", lines[-1]))
    s = _UNSAFE.sub(" ", _UUID.sub("uuid", s))
    return " ".join(s.split())[:200]


def outcome_of(returncode: int, counts: dict[str, Any] | None, identity: Identity | None = None) -> str:
    if returncode == 0:
        return "ok" if counts is not None else "bad_output"
    if returncode in (identity.refused_exits if identity is not None else frozenset({2})):
        return "refused"
    return "partial" if returncode == 1 else "crashed"


def is_alert(outcome: str, counts: dict[str, Any] | None) -> bool:
    stale = (counts or {}).get("stale_estimate")
    return outcome in ALERT or (isinstance(stale, (int, float)) and not isinstance(stale, bool) and stale > 0)


@dataclass
class RunResult:
    job: str
    outcome: str
    exit_code: int
    started_at: str
    finished_at: str
    duration_ms: int
    counts: dict[str, Any] | None = None
    child_exit: int | None = None
    reason: str = ""
    level: str = field(init=False)

    def __post_init__(self) -> None:
        self.level = "alert" if is_alert(self.outcome, self.counts) else "info"

    def log_line(self) -> str:
        doc = {"event": "job_run", "level": self.level, "job": self.job, "outcome": self.outcome, "exit_code": self.exit_code,
               "child_exit": self.child_exit, "started_at": self.started_at, "finished_at": self.finished_at,
               "duration_ms": self.duration_ms, "counts": self.counts}
        if self.reason:
            doc["reason"] = self.reason
        return json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class LockHandle:
    """acquired：取没取到；probe：锁连接还活着吗（运行期间定期调用）"""
    acquired: bool
    probe: Callable[[], Awaitable[bool]]


LockFactory = Callable[[Job, Mapping[str, str]], "contextlib.AbstractAsyncContextManager[LockHandle]"]


@contextlib.asynccontextmanager
async def pg_advisory_lock(job: Job, env: Mapping[str, str]) -> AsyncIterator[LockHandle]:
    """按该任务身份的连接取会话级咨询锁；取不到 acquired = False。连上后先核对服务端地址"""
    import asyncpg
    conn = await asyncpg.connect(env[job.identity.url_var], timeout=10,
                                 server_settings={"application_name": f"starbuddy-scheduler:{job.name}"})

    async def probe() -> bool:
        try:
            return await asyncio.wait_for(conn.fetchval("SELECT 1"), PROBE_TIMEOUT) == 1
        except Exception:  # noqa: BLE001 连接断开、超时：都按锁已丢失
            return False
    try:
        await check_server_local(conn)
        got = bool(await conn.fetchval("SELECT pg_try_advisory_lock($1::bigint)", lock_key(job.name)))
        try:
            yield LockHandle(got, probe)
        finally:
            if got:
                with contextlib.suppress(Exception):
                    await conn.fetchval("SELECT pg_advisory_unlock($1::bigint)", lock_key(job.name))
    finally:
        with contextlib.suppress(Exception):
            await conn.close()


class Runner:
    def __init__(self, environ: Mapping[str, str], *, out: TextIO | None = None, lock: LockFactory = pg_advisory_lock,
                 interpreters: Mapping[str, tuple[Path, Path]] | None = None, probe_seconds: float = PROBE_SECONDS,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.environ = dict(environ)
        self.out = out if out is not None else sys.stdout
        self.lock = lock
        self.interpreters = dict(interpreters or {})
        self.probe_seconds = probe_seconds
        self.clock = clock

    def interpreter(self, job: Job) -> tuple[Path, Path]:
        """(解释器, 工作目录)：services/<服务>/.venv/bin/python，工作目录为服务根"""
        svc = job.identity.service
        return self.interpreters.get(svc) or (SERVICES / svc / ".venv" / "bin" / "python", SERVICES / svc)

    def argv(self, job: Job) -> list[str]:
        return [str(self.interpreter(job)[0]), "-m", job.module, *job.args]

    def emit(self, r: RunResult) -> RunResult:
        self.out.write(r.log_line() + "\n")
        self.out.flush()
        return r

    def check(self, job: Job) -> dict[str, str]:
        return preflight(job, self.environ, self.interpreter(job)[0])

    async def run(self, job: Job, *, dry_run: bool = False) -> RunResult:
        start, t0 = self.clock(), time.monotonic()

        def done(outcome: str, **kw: Any) -> RunResult:
            return self.emit(RunResult(job.name, outcome, EXIT[outcome], _iso(start), _iso(self.clock()),
                                       int((time.monotonic() - t0) * 1000), **kw))
        try:
            env = self.check(job)
        except PreflightError as e:
            return done("refused", reason=str(e)[:200])
        if dry_run:
            return done("dry_run", reason=" ".join([job.module, *job.args]))
        try:
            async with self.lock(job, env) as handle:
                if not handle.acquired:
                    return done("skipped_locked")
                return await self._child(job, env, handle, done)
        except PreflightError as e:
            return done("refused", reason=str(e)[:200])
        except Exception as e:  # noqa: BLE001 取锁失败（连不上库等）：只记错误类别，不带消息原文
            return done("lock_error", reason=type(e).__name__[:48])

    async def _child(self, job: Job, env: Mapping[str, str], handle: LockHandle, done: Callable[..., RunResult]) -> RunResult:
        cwd = self.interpreter(job)[1]
        proc = await asyncio.create_subprocess_exec(*self.argv(job), cwd=str(cwd), env=dict(env), stdin=asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                    start_new_session=True, limit=MAX_OUTPUT)
        comm = asyncio.ensure_future(proc.communicate())
        loop = asyncio.get_running_loop()
        deadline = loop.time() + job.timeout_seconds
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                await _kill_group(proc, comm)
                return done("timeout", reason=f"timeout_after_{int(job.timeout_seconds)}s")
            finished, _ = await asyncio.wait({comm}, timeout=min(self.probe_seconds, remaining))
            if finished:
                break
            if loop.time() < deadline and not await handle.probe():
                await _kill_group(proc, comm)
                return done("lock_lost", reason="lock_connection_lost")
        stdout, stderr = comm.result()
        rc = proc.returncode if proc.returncode is not None else -1
        counts = parse_counts(stdout[-MAX_OUTPUT:])
        outcome = outcome_of(rc, counts, job.identity)
        return done(outcome, counts=counts, child_exit=rc, reason="" if outcome == "ok" else stderr_tail(stderr))


async def _kill_group(proc: asyncio.subprocess.Process, comm: asyncio.Future) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, sig)
        try:
            await asyncio.wait_for(proc.wait(), timeout=KILL_GRACE_SECONDS)
            break
        except asyncio.TimeoutError:
            continue
    with contextlib.suppress(Exception):
        await asyncio.wait_for(asyncio.shield(comm), timeout=KILL_GRACE_SECONDS)
    if not comm.done():
        comm.cancel()
