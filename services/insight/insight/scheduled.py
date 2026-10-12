"""定时任务入口（不在契约里、不对外开放）：考前预测冻结（0034 insight.freeze_due_predictions）与到期删除执行
（0035 platform.execute_due_erasures）。两个库函数只授 starbuddy_scheduler，所以这里用调度器自己的连接，不用洞察服务的连接池：
- 登录角色只继承 starbuddy_scheduler（连接后 SET ROLE starbuddy_scheduler）；断言不是超级用户、没有 BYPASSRLS / CREATEROLE / CREATEDB、
  不继承其他应用角色（尤其是 starbuddy_insight：判断只由洞察服务写，调度器不能写）、不是 pg_ 预置角色成员，否则拒绝执行；
- 时间取数据库 now()（库函数要求 p_now 不晚于现在、最多往前 1 天）；每次调用一个事务。
调度频率：冻结在考试当天零点（上海）之后尽快；删除在冷静期满后 24 小时内（spec 待人确认 #3，靠调度频率保证）。

PLT-13 第一步（specs/scheduler-plt13）补了同一身份的另外三个入口（库函数与授权都不改，只调用）：
- 计量预留过期 platform.meter_expire_due、订阅续期 platform.meter_renew_due（0009 / 0015，只授 starbuddy_scheduler）：
  小批量循环（每批一个事务，持锁时间短），一批不满即停——与 services/bff/src/metering/jobs.ts 的 runExpiry / runRenewal 同一语义；
- 判分抽检 platform.review_sample_spot_checks（0023，只授 starbuddy_scheduler）：按学科逐个调用，比例由调用方给（没有代码缺省值）。
命令行（services/scheduler 按时间表以子进程调用；也可手动，只连本机数据库）：

  SCHEDULER_ENV=dev SCHEDULER_DATABASE_URL=postgresql://...@localhost/... python -m insight.scheduled freeze
  ... python -m insight.scheduled erasures
  ... python -m insight.scheduled meter-expire [--batch 50]
  ... python -m insight.scheduled meter-renew [--batch 50]
  ... python -m insight.scheduled spot-check --subject aya [--subject wenli ...] --ratio 0.1 (--lookback-hours 26 | --since <ISO 8601>)

规则 9：SCHEDULER_ENV 只收 dev、test；连接前核对连接串里的全部主机、连上之后核对服务端地址（复用审核台命令行的 local_only /
check_server_local，同 insight.jobs）；连接的登录角色断言同上。输出一行 JSON，只有计数，不含学生 ID。
退出码：0 正常；1 计量循环到了批次上限仍有到期（下一轮接着处理）；2 参数、环境、本机核对或库拒绝。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from typing import Any, Sequence

import asyncpg

ROLE = "starbuddy_scheduler"
OTHER_APP_ROLES = ("starbuddy_insight", "starbuddy_bff", "starbuddy_learning", "starbuddy_review", "starbuddy_kge",
                   "starbuddy_kge_read", "starbuddy_report", "starbuddy_authz", "starbuddy_eraser")


class SchedulerRoleError(RuntimeError):
    """调度器连接的角色不安全：拒绝执行"""


async def assert_scheduler_role(conn: asyncpg.Connection) -> None:
    row = await conn.fetchrow(
        """SELECT current_user::text AS cur, r.rolsuper, r.rolbypassrls, r.rolcreaterole, r.rolcreatedb,
                  pg_has_role(session_user, $1, 'MEMBER') AS is_scheduler,
                  ARRAY(SELECT x FROM unnest($2::text[]) x WHERE pg_has_role(session_user, x, 'MEMBER')) AS others,
                  ARRAY(SELECT p.rolname::text FROM pg_roles p
                         WHERE p.rolname LIKE 'pg\\_%' AND pg_has_role(session_user, p.oid, 'MEMBER')) AS predefined
             FROM pg_roles r WHERE r.rolname = session_user""", ROLE, list(OTHER_APP_ROLES))
    if row is None or row["rolsuper"] or row["rolbypassrls"] or row["rolcreaterole"] or row["rolcreatedb"]:
        raise SchedulerRoleError("调度器的登录角色不能是超级用户，不能有 BYPASSRLS / CREATEROLE / CREATEDB")
    if row["predefined"] or row["others"] or not row["is_scheduler"] or row["cur"] != ROLE:
        raise SchedulerRoleError(f"调度器只能以 {ROLE} 执行，且不能继承其他应用角色或 pg_ 预置角色")


async def connect_scheduler(dsn: str | None = None, **connect: Any) -> asyncpg.Connection:
    conn = await asyncpg.connect(dsn, server_settings={"application_name": "starbuddy-insight-scheduler", "TimeZone": "Asia/Shanghai"},
                                 **connect)
    try:
        await conn.execute(f"SET ROLE {ROLE}")
        await assert_scheduler_role(conn)
    except BaseException:
        await conn.close()
        raise
    return conn


async def freeze_due_predictions(conn: asyncpg.Connection, now: datetime | None = None) -> int:
    """考试当天零点（上海）起冻结预测；返回冻结条数。冻结后预测不可改、不可删（库层守卫）"""
    await assert_scheduler_role(conn)
    return int(await conn.fetchval("SELECT insight.freeze_due_predictions(coalesce($1::timestamptz, now()))", now))


async def execute_due_erasures(conn: asyncpg.Connection, now: datetime | None = None) -> int:
    """冷静期满的删除请求逐个执行（一个事务删完该学生全部个人层行并写回执）；返回执行条数"""
    await assert_scheduler_role(conn)
    return int(await conn.fetchval("SELECT platform.execute_due_erasures(coalesce($1::timestamptz, now()))", now))


# ---------- PLT-13 第一步：计量过期、续期与判分抽检（库函数不改，只调用） ----------
METER_FUNCS = {"meter-expire": "platform.meter_expire_due", "meter-renew": "platform.meter_renew_due"}
SUBJECTS = ("aya", "zhixing", "wenli", "echo")  # platform.subject（0001）
MAX_BATCH = 1000                                 # 库函数的批量上限（0009）
MAX_LOOKBACK_HOURS = 31 * 24                     # 抽检的 since 最多往前 31 天（0023）


async def _meter_loop(conn: asyncpg.Connection, func: str, batch: int, max_batches: int) -> tuple[int, int, bool]:
    """小批量循环（同 BFF metering/jobs.ts）：每批一个事务；一批不满即停。返回 (处理条数, 批次数, 是否到了批次上限仍可能有到期)"""
    if not 1 <= batch <= MAX_BATCH or max_batches < 1:
        raise ValueError("batch must be 1-1000 and max_batches positive")
    await assert_scheduler_role(conn)
    total = 0
    for i in range(1, max_batches + 1):
        n = int(await conn.fetchval(f"SELECT {func}($1::integer)", batch))
        total += n
        if n < batch:
            return total, i, False
    return total, max_batches, True


async def meter_expire_due(conn: asyncpg.Connection, batch: int = 50, max_batches: int = 10_000) -> tuple[int, int, bool]:
    """计量预留过期释放（0009 platform.meter_expire_due）"""
    return await _meter_loop(conn, METER_FUNCS["meter-expire"], batch, max_batches)


async def meter_renew_due(conn: asyncpg.Connection, batch: int = 50, max_batches: int = 10_000) -> tuple[int, int, bool]:
    """订阅续期与到期（0015 platform.meter_renew_due）"""
    return await _meter_loop(conn, METER_FUNCS["meter-renew"], batch, max_batches)


async def sample_spot_checks(conn: asyncpg.Connection, subject: str, ratio: float, since: datetime | None = None,
                             lookback_hours: int | None = None) -> int:
    """判分抽检进统一复核队列（0023 platform.review_sample_spot_checks）：按判分 ID 哈希确定性抽样，已有复核任务的不再进队，
    所以同一窗口重复运行不重复进队。since 与 lookback_hours 给一个；lookback 按数据库 now() 往前推"""
    if subject not in SUBJECTS:
        raise ValueError("unknown subject")
    if (since is None) == (lookback_hours is None):
        raise ValueError("give exactly one of since / lookback_hours")
    await assert_scheduler_role(conn)
    return int(await conn.fetchval(
        "SELECT platform.review_sample_spot_checks($1::platform.subject, coalesce($2::timestamptz, now() - make_interval(hours => $3::integer)),"
        " $4::numeric)", subject, since, lookback_hours, ratio))


# ---------- 命令行 ----------
ENV_VAR, URL_VAR = "SCHEDULER_ENV", "SCHEDULER_DATABASE_URL"
ALLOWED_ENVS = ("dev", "test")
LOCAL_ONLY = "调度器定时任务只连本机数据库（localhost、回环地址或 Unix socket）"


class CliError(Exception):
    """参数、环境或本机核对不通过：不连库或中止，退出码 2"""


def _local_checks():
    from .shared import forbidden_matcher  # noqa: F401  先导入 shared：把 services/learning 加进 sys.path（复用审核台的本机核对）
    from review_console.__main__ import NOT_LOCAL, check_server_local, local_only
    return NOT_LOCAL, local_only, check_server_local


def _rephrase(e: SystemExit, not_local: str) -> CliError:
    text = str(e.code)
    if text.startswith(not_local):
        return CliError(LOCAL_ONLY + text[len(not_local):])
    if "postgresql://" in text:
        return CliError(f"{URL_VAR} 必须是 postgresql:// 连接串")
    return CliError(LOCAL_ONLY)


def check_env(environ: dict[str, str]) -> str:
    """SCHEDULER_ENV 只收 dev / test；连接串里的全部主机都必须是本机（连接前）。返回连接串"""
    if environ.get(ENV_VAR, "") not in ALLOWED_ENVS:
        raise CliError(f"{ENV_VAR} 必须是 {ALLOWED_ENVS} 之一；本命令不连接 pilot 或生产环境")
    url = environ.get(URL_VAR, "")
    if not url:
        raise CliError(f"缺少 {URL_VAR}")
    not_local, local_only, _ = _local_checks()
    try:
        return local_only(url)
    except SystemExit as e:
        raise _rephrase(e, not_local) from None


async def check_server(conn: Any) -> None:
    """连上之后：服务端地址为空（Unix socket）或回环地址，否则中止"""
    not_local, _, check_server_local = _local_checks()
    try:
        await check_server_local(conn)
    except SystemExit as e:
        raise _rephrase(e, not_local) from None


def _since(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    try:
        v = datetime.fromisoformat(raw)
    except ValueError:
        raise CliError("--since 必须是带时区的 ISO 8601 时间") from None
    if v.tzinfo is None:
        raise CliError("--since 必须带时区")
    return v


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m insight.scheduled", description="调度器身份的定时任务（只连本机数据库，不接生产调度）")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("freeze", help="考前预测冻结（insight.freeze_due_predictions）")
    sub.add_parser("erasures", help="到期删除执行（platform.execute_due_erasures）")
    for name, help_ in (("meter-expire", "计量预留过期释放（platform.meter_expire_due）"), ("meter-renew", "订阅续期（platform.meter_renew_due）")):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--batch", type=int, default=50, help="每批条数（1–1000，每批一个事务）")
        sp.add_argument("--max-batches", type=int, default=10_000, help="本轮最多批次（到了上限仍有到期时退出码 1，下一轮接着处理）")
    sc = sub.add_parser("spot-check", help="判分抽检进统一复核队列（platform.review_sample_spot_checks）")
    sc.add_argument("--subject", action="append", required=True, choices=SUBJECTS)
    sc.add_argument("--ratio", type=float, required=True, help="抽检比例 (0, 0.5]（库函数核对）；没有代码缺省值，由时间表给")
    g = sc.add_mutually_exclusive_group(required=True)
    g.add_argument("--lookback-hours", type=int, help=f"从数据库当前时间往前推的小时数（1–{MAX_LOOKBACK_HOURS}）")
    g.add_argument("--since", help="起点（带时区的 ISO 8601；最多往前 31 天，库函数核对）")
    return p.parse_args(argv)


async def _run(a: argparse.Namespace, url: str) -> dict[str, Any]:
    conn = await connect_scheduler(url)  # 角色断言不过即关闭连接并报错
    try:
        await check_server(conn)
        if a.cmd == "freeze":
            return {"job": "freeze", "frozen": await freeze_due_predictions(conn)}
        if a.cmd == "erasures":
            return {"job": "erasures", "executed": await execute_due_erasures(conn)}
        if a.cmd in METER_FUNCS:
            total, batches, more = await _meter_loop(conn, METER_FUNCS[a.cmd], a.batch, a.max_batches)
            key = "expired" if a.cmd == "meter-expire" else "renewed"
            return {"job": a.cmd, key: total, "batches": batches, "more_due": more}
        since = _since(a.since)
        enqueued = {}
        for s in dict.fromkeys(a.subject):
            enqueued[s] = await sample_spot_checks(conn, s, a.ratio, since, a.lookback_hours)
        return {"job": "spot-check", "enqueued": enqueued}
    finally:
        await conn.close()


def main(argv: Sequence[str] | None = None, environ: dict[str, str] | None = None) -> int:
    a = _parse(argv)
    try:
        if a.cmd in METER_FUNCS and not (1 <= a.batch <= MAX_BATCH and 1 <= a.max_batches <= 1_000_000):
            raise CliError("--batch 必须在 1–1000 之间，--max-batches 必须在 1–1000000 之间")
        if a.cmd == "spot-check":
            if a.lookback_hours is not None and not 1 <= a.lookback_hours <= MAX_LOOKBACK_HOURS:
                raise CliError(f"--lookback-hours 必须在 1–{MAX_LOOKBACK_HOURS} 之间")
            _since(a.since)
        url = check_env(dict(os.environ if environ is None else environ))
        out = asyncio.run(_run(a, url))
    except CliError as e:
        print(f"insight.scheduled：{e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 库拒绝、角色断言不过等：只报错误类别与 SQLSTATE，不带消息原文（可能带数据）
        state = getattr(e, "sqlstate", "") or ""
        print(f"insight.scheduled：执行失败（{type(e).__name__}{' ' + state if state else ''}）", file=sys.stderr)
        return 2
    sys.stdout.write(json.dumps(out, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    return 1 if out.get("more_due") else 0


if __name__ == "__main__":
    sys.exit(main())
