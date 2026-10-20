"""命令行：洞察服务的定时任务入口（SIE-02b 第二步，spec“第二步补充”默认方案第 5 条，待人确认 S2-4 默认：只提供命令、不接生产调度）。

  INSIGHT_ENV=dev INSIGHT_DATABASE_URL=postgresql://...@localhost/... python -m insight.jobs sync (--student <uuid> ... | --all)
  INSIGHT_ENV=dev INSIGHT_DATABASE_URL=... python -m insight.jobs sweep [--limit 1000]
  INSIGHT_ENV=dev INSIGHT_DATABASE_URL=... python -m insight.jobs metrics (--student <uuid> ... | --all) [--skip <指标名> ...]
  INSIGHT_ENV=dev INSIGHT_DATABASE_URL=... python -m insight.jobs weekly [--week-start 2026-10-05]

- sync：判断生成接真实掌握（jobs.sync_mastery_from_db）：读该学生全部当前节点键掌握行，生成与流转判断；
- sweep：每天的到期扫描（jobs.sweep）：到期的假设 / 成立判断转待验证；
- metrics：中圈指标静默重算（jobs.recompute_metrics）：结果只写 visibility = {agent} 的指标判断；题目打标注入要等待人确认 E，
  不注入时独立解题率与迁移差不出结果。--skip（可重复，SIE-05）：本次不算的指标，只收 metrics.py CALCULATORS 里的名字，
  其他名字退出码 2；被跳过的指标已有判断不动、照常到期。调度器的定时作业用 --skip persistence 暂不重算学习持续性
  （与迁移 0025“时间戳只用于降负触发”的关系待人确认）。
- weekly（PLT-13 第一步，specs/scheduler-plt13）：每周家长周报与学生成长手记（jobs.weekly_reports，不改其逻辑）：缺省为上一个已结束的周
  （上海时区，按数据库日期）；学生取“有对学生或家长可见的节点判断的全部学生”。判断策略同 sync（INSIGHT_POLICY），周报配置
  INSIGHT_WEEKLY_POLICY（缺省 config/weekly.synthetic.json）、一致性检查词表 INSIGHT_WEEKLY_CHECK（缺省 config/weekly-check.synthetic.json）；
  合成占位只允许 test，未签字的周报配置在 test 以外不生成（jobs.weekly_reports 的守卫）。不触发学情档案页编译（不配学习服务，
  profile_pages 为空，与服务没配学习服务时同一行为）。重复运行同一周不重写；有学生被库里守卫拒收（failed）时退出码 1。
--all 取“有当前节点键掌握行的全部学生”（platform.mastery_estimate，0028 已授洞察服务只读）。
学习服务掌握重算完成后不直接调洞察服务（spec 默认方案第 5 条）；什么时候跑由人接调度（同 4-G 时政下架的做法）。

规则 9：只连本机数据库。连接前核对连接串里的全部主机（复用审核台命令行的 local_only：多主机写法、host 查询参数、
hostaddr / service 一律拒），连上之后再核对服务端地址（check_server_local）；连接池的登录角色只继承 starbuddy_insight
（store.create_pool 的角色断言）。INSIGHT_ENV 只收 dev、test；判断策略配置同服务（INSIGHT_POLICY，合成占位只允许 test）。
输出一行 JSON：任务名、学生数与按原因计的写入条数，不含学生 ID、判断 ID 或任何内容。退出码：0 正常；1 周报有学生被拒收；
2 参数、环境或库拒绝。
注意：--student 传的学生 ID 会留在 shell 历史与进程列表（ps）里，只限本机合成库使用（规则 8）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Sequence

from .policy import CONFIG_DIR, PolicyError, load_check_config, load_policy
from .service.config import SERVICE_ENVS
from .shared import forbidden_matcher  # noqa: F401  先导入 shared：把 services/learning 加进 sys.path（复用审核台的本机核对）

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
LOCAL_ONLY = "洞察服务定时任务只连本机数据库（localhost、回环地址或 Unix socket）"
STUDENTS_SQL = """SELECT DISTINCT m.student_id::text AS student_id FROM platform.mastery_estimate m
                   WHERE m.node_id IS NOT NULL AND m.is_current ORDER BY 1"""


class JobError(Exception):
    """参数、环境或本机核对不通过：不连库或中止，退出码 2"""


def _rephrase(e: SystemExit) -> JobError:
    """审核台命令行的核对以 SystemExit 报错（说明以时政下架任务的口吻写）；换成本命令的说明，保留具体原因"""
    from review_console.__main__ import NOT_LOCAL
    text = str(e.code)
    if text.startswith(NOT_LOCAL):
        return JobError(LOCAL_ONLY + text[len(NOT_LOCAL):])
    if "postgresql://" in text:
        return JobError("INSIGHT_DATABASE_URL 必须是 postgresql:// 连接串")
    return JobError(LOCAL_ONLY)


def check_local_url(url: str, env: str | None = None) -> str:
    """连接前：连接串里出现的与 asyncpg 实际会连的全部主机都必须是本机（review_console.__main__.local_only）；
    staging 另收内网 IPv4 字面量（LT-01 决定记录第 5 条）"""
    from review_console.__main__ import local_only
    try:
        return local_only(url, env)
    except SystemExit as e:
        raise _rephrase(e) from None


async def check_server(conn: Any, env: str | None = None) -> None:
    """连上之后：服务端地址为空（Unix socket）或回环地址（staging 另收内网 IPv4），否则中止（名字解析到别处、端口转发等）"""
    from review_console.__main__ import check_server_local
    try:
        await check_server_local(conn, env)
    except SystemExit as e:
        raise _rephrase(e) from None


def _env() -> tuple[str, str]:
    env = os.environ.get("INSIGHT_ENV", "")
    if env not in SERVICE_ENVS:
        raise JobError(f"INSIGHT_ENV 必须是 {SERVICE_ENVS} 之一；本命令不连接 pilot 或生产环境")
    url = os.environ.get("INSIGHT_DATABASE_URL", "")
    if not url:
        raise JobError("缺少 INSIGHT_DATABASE_URL")
    return env, check_local_url(url, env)


def _students(raw: Sequence[str] | None) -> list[str]:
    out = list(dict.fromkeys(s.lower() for s in raw or ()))
    if any(not UUID_RE.fullmatch(s) for s in out):
        raise JobError("--student 必须是 UUID")
    return out


def _skip(raw: Sequence[str] | None) -> tuple[str, ...]:
    from .metrics import METRIC_NAMES
    out = tuple(dict.fromkeys(raw or ()))
    if any(s not in METRIC_NAMES for s in out):
        raise JobError(f"--skip 只接受 {', '.join(METRIC_NAMES)}")
    return out


def _week_start(raw: str | None) -> date | None:
    if raw is None:
        return None
    try:
        d = date.fromisoformat(raw)
    except ValueError:
        raise JobError("--week-start 必须是 YYYY-MM-DD") from None
    if d.isoweekday() != 1:
        raise JobError("--week-start 必须是周一")
    return d


def _weekly_configs(env: str) -> tuple[Any, Any]:
    from .weekly import load_weekly_policy
    try:
        weekly = load_weekly_policy(Path(os.environ.get("INSIGHT_WEEKLY_POLICY") or CONFIG_DIR / "weekly.synthetic.json"), env=env)
        check = load_check_config(Path(os.environ.get("INSIGHT_WEEKLY_CHECK") or CONFIG_DIR / "weekly-check.synthetic.json"), env=env)
    except (PolicyError, OSError, ValueError) as e:
        raise JobError(f"周报配置不可用：{type(e).__name__}") from None
    return weekly, check


async def _weekly(store: Any, conn_pool: Any, env: str, week: date | None, policy: Any, weekly: Any, check: Any) -> dict[str, Any]:
    from . import jobs
    from .store_sie03 import Unavailable
    from .weekly import week_of
    if week is None:
        async with conn_pool.acquire() as conn:
            today = await conn.fetchval("SELECT (now() AT TIME ZONE 'Asia/Shanghai')::date")
        week = week_of(today)[0] - timedelta(days=7)  # 上一个已结束的周
    from .store_weekly import check_week
    try:  # 只把周参数的核对（与 run_week 里同一个 check_week）转成参数错；周报生成过程中的 ValueError 按原样归类
        await check_week(store, week)
    except ValueError:
        raise JobError("周只接受已结束、35 天内的周一") from None
    try:
        run = await jobs.weekly_reports(store, week, policy, weekly, check, env=env)
    except Unavailable:
        raise JobError("周报配置未签字：test 以外不生成") from None
    statuses = ("generated", "withheld", "empty", "exists", "failed")
    return {"job": "weekly", "week_start": week.isoformat(), "students": len({o.student_id for o in run.outcomes}),
            "outcomes": {s: run.count(s) for s in statuses}, "sampled": run.sampled, "profile_pages": dict(run.profile_pages)}


async def _run(cmd: str, students: list[str], all_students: bool, limit: int, week: date | None = None,
               skip: tuple[str, ...] = ()) -> dict[str, Any]:
    from . import jobs
    from .store import InsightStore, create_pool

    env, url = _env()
    policy = None
    if cmd in ("sync", "metrics", "weekly"):
        try:
            policy = load_policy(Path(os.environ.get("INSIGHT_POLICY") or CONFIG_DIR / "claims.synthetic.json"), env=env)
        except PolicyError as e:
            raise JobError(f"判断策略配置不可用：{e}") from None
    weekly_cfg = _weekly_configs(env) if cmd == "weekly" else None  # 连库之前先核对周报配置
    pool = await create_pool(url)  # 角色断言：只继承 starbuddy_insight，否则关闭池并报错
    try:
        async with pool.acquire() as conn:
            await check_server(conn, env)
            if all_students:
                students = [r["student_id"] for r in await conn.fetch(STUDENTS_SQL)]
        store = InsightStore(pool)
        if cmd == "sweep":
            return {"job": "sweep", "to_verify": await jobs.sweep(store, limit)}
        if cmd == "weekly":
            assert weekly_cfg is not None
            return await _weekly(store, pool, env, week, policy, *weekly_cfg)
        reasons: Counter[str] = Counter()
        skipped = 0
        missing: set[str] = set()
        results = 0
        for s in students:
            if cmd == "sync":
                r = await jobs.sync_mastery_from_db(store, s, policy)
            else:
                metric_results, r = await jobs.recompute_metrics(store, s, policy, skip=skip)
                results += len(metric_results)
            reasons.update(op.reason for op in r.ops)
            skipped += r.skipped_rows
            missing.update(r.missing_templates)
        out: dict[str, Any] = {"job": cmd, "students": len(students), "writes": dict(sorted(reasons.items())),
                               "missing_templates": sorted(missing)}
        if cmd == "sync":
            out["skipped_rows"] = skipped
        else:
            out["metric_results"] = results
            out["skipped_metrics"] = sorted(skip)
        return out
    finally:
        await pool.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m insight.jobs", description="洞察服务定时任务（只连本机数据库，不接生产调度）")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, help_ in (("sync", "按当前节点键掌握行生成与流转判断"), ("metrics", "中圈指标静默重算（只写 agent 可见的指标判断）")):
        sp = sub.add_parser(name, help=help_)
        g = sp.add_mutually_exclusive_group(required=True)
        g.add_argument("--student", action="append", help="学生 ID（可重复；会留在 shell 历史与进程列表里，只限本机合成库）")
        g.add_argument("--all", action="store_true", help="有当前节点键掌握行的全部学生")
        if name == "metrics":
            sp.add_argument("--skip", action="append", help="本次不算的指标（可重复；只收 metrics.py CALCULATORS 里的名字）")
    sw =sub.add_parser("sweep", help="到期扫描：到期的假设 / 成立判断转待验证")
    sw.add_argument("--limit", type=int, default=1000)
    wk = sub.add_parser("weekly", help="每周家长周报与学生成长手记（缺省上一个已结束的周）")
    wk.add_argument("--week-start", help="周一日期 YYYY-MM-DD（只接受已结束、35 天内的周）")
    a = p.parse_args(argv)
    try:
        if a.cmd == "sweep" and not 1 <= a.limit <= 100_000:
            raise JobError("--limit 必须在 1–100000 之间")
        students = _students(getattr(a, "student", None))
        week = _week_start(getattr(a, "week_start", None))
        skip = _skip(getattr(a, "skip", None))
        out = asyncio.run(_run(a.cmd, students, bool(getattr(a, "all", False)), getattr(a, "limit", 1000), week, skip))
    except JobError as e:
        print(f"insight.jobs：{e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 库拒绝、角色断言不过等：只报错误类别与 SQLSTATE，不带消息原文（可能带数据）
        state = getattr(e, "sqlstate", "") or ""
        print(f"insight.jobs：执行失败（{type(e).__name__}{' ' + state if state else ''}）", file=sys.stderr)
        return 2
    sys.stdout.write(json.dumps(out, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    return 1 if out["job"] == "weekly" and out["outcomes"]["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
