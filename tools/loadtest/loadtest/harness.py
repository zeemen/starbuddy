"""BFF 计量驱动桩（node/meter-harness.mts）的启动与调用：批改一律经 BFF 的 meteredCall（契约审查 H1）。

meteredCall 自己完成预留、同键退避重试、结算 / 释放 / 留给过期任务与过期后只查不执行；这里只把结果归类给报表：
  ok                       确定成功（charged 表示是否扣次；dedup / 重放不扣）
  failed                   确定失败（已释放预留）
  unknown                  结果未知（未结算、未释放，留给过期任务）
  failed_previously / expired_retry_with_new_key / insufficient / … 其余照 BFF 的状态名原样记
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[3]
HARNESS = ROOT / "tools/loadtest/node/meter-harness.mts"
REJECTED = frozenset({"insufficient", "daily_cap_reached", "curfew_soon", "curfew", "retained_no_ai", "no_entitlement",
                      "conflict", "dedup_in_progress"})


def available() -> str | None:
    if not shutil.which("node"):
        return "没有 node"
    if not (ROOT / "services/bff/node_modules/pg").exists():
        return "没有安装 BFF 依赖（cd services/bff && npm ci）"
    return None


def free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def meter_harness(database_url: str, learning_url: str, token: str, meter_now_ms, *, retry_scale: float = 1.0,
                  timeout_ms: int = 15_000, log: Path | None = None, pool_max: int = 20):
    """meter_now_ms：无参函数，返回数据库计量时钟的当前毫秒（管理员连接读取），用于对齐 meteredCall 的 now"""
    port = free_port()
    env = {"PATH": os.environ["PATH"], "PORT": str(port), "HARNESS_DATABASE_URL": database_url, "HARNESS_LEARNING_URL": learning_url,
           "HARNESS_LEARNING_TOKEN": token, "HARNESS_RETRY_SCALE": str(retry_scale), "HARNESS_TIMEOUT_MS": str(timeout_ms),
           "HARNESS_POOL_MAX": str(pool_max)}
    out = open(log, "wb") if log else subprocess.DEVNULL
    proc = subprocess.Popen(["node", "--no-warnings", str(HARNESS)], cwd=HARNESS.parent, env=env, stdout=out,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    try:
        deadline = time.time() + 20
        while True:
            try:
                if httpx.post(f"http://127.0.0.1:{port}/clock-sync", json={"meter_now_ms": meter_now_ms()}, timeout=2,
                              trust_env=False).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if proc.poll() is not None or time.time() > deadline:
                raise RuntimeError("计量驱动桩没有启动")
            time.sleep(0.1)
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        if log:
            out.close()


def classify(status: str) -> str:
    """meteredCall 结果 → 报表类别"""
    if status == "ok":
        return "ok"
    if status in REJECTED:
        return "rejected"
    if status == "unknown":
        return "http_5xx"
    return "http_4xx"


async def metered_grade(client: httpx.AsyncClient, harness_url: str, *, account: str, student: str, key: str, body: dict,
                        item_ref: str) -> dict:
    r = await client.post(f"{harness_url}/metered-call", json={
        "accountId": account, "studentId": student, "resourceType": "mistake_analysis", "idempotencyKey": key,
        "body": body, "itemRef": item_ref}, timeout=600)
    r.raise_for_status()
    return r.json()


async def clock_sync(client: httpx.AsyncClient, harness_url: str, meter_now_ms: int) -> None:
    """计量时钟快进后重新对齐"""
    (await client.post(f"{harness_url}/clock-sync", json={"meter_now_ms": meter_now_ms})).raise_for_status()
