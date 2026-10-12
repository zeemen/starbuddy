"""命令行：

  SCHEDULER_ENV=dev <各身份的环境变量> python -m scheduler                       常驻，按时间表运行全部任务
  SCHEDULER_ENV=dev ... python -m scheduler --once meter-expire                  立刻运行一个任务一轮（取锁、子进程、日志）后退出
  SCHEDULER_ENV=dev ... python -m scheduler --dry-run [--once <任务>]             只核对并列出计划（命令、变量名、接下来 3 次运行时刻），不连库
  可选 --schedule <路径>（缺省 config/schedule.synthetic.yaml）

启动核对（规则 9）：SCHEDULER_ENV 只收 dev / test；时间表配置合法；选中的每个任务所需的变量都在、各身份的运行环境变量只收 dev / test、
全部连接串是本机、子进程解释器存在——任一不过拒绝启动（退出码 2），不连库、不起子进程。
退出码：--once 时为该任务一轮的退出码（0 正常或跳过；1 部分失败、超时、崩溃、输出不对；2 拒绝）；常驻与 --dry-run 正常结束为 0；启动核对不过为 2。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Callable, Mapping, TextIO

from .config import DEFAULT_SCHEDULE, ConfigError, load_config
from .loop import Loop, serve_forever
from .runner import ALLOWED_ENVS, PreflightError, Runner, child_env
from .schedule import upcoming

ENV_VAR = "SCHEDULER_ENV"


def _refuse(msg: str) -> int:
    print(f"scheduler：{msg}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None, environ: Mapping[str, str] | None = None, *, out: TextIO | None = None,
         clock: Callable[[], datetime] | None = None, runner: Runner | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m scheduler", description="StarBuddy 调度器（只连本机数据库，不接生产调度）")
    p.add_argument("--schedule", default=str(DEFAULT_SCHEDULE), help="时间表配置（YAML）")
    p.add_argument("--once", metavar="JOB", help="立刻运行这个任务一轮后退出")
    p.add_argument("--dry-run", action="store_true", help="只核对并列出计划，不连库、不起子进程")
    a = p.parse_args(argv)
    env = dict(os.environ if environ is None else environ)
    out = out if out is not None else sys.stdout
    clock = clock or (lambda: datetime.now(timezone.utc))
    if env.get(ENV_VAR, "") not in ALLOWED_ENVS:
        return _refuse(f"{ENV_VAR} 必须是 {ALLOWED_ENVS} 之一；调度器不接 pilot 或生产环境（规则 9）")
    try:
        cfg = load_config(a.schedule)
    except ConfigError as e:
        return _refuse(f"时间表配置不合法：{e}")
    if a.once is not None and a.once not in cfg.jobs:
        return _refuse(f"没有任务 {a.once!r}（有：{', '.join(sorted(cfg.jobs))}）")
    selected = [cfg.jobs[a.once]] if a.once else list(cfg.jobs.values())
    run = runner or Runner(env, out=out, clock=clock)
    problems = []
    for job in selected:
        try:
            run.check(job)
        except PreflightError as e:
            problems.append(str(e))
    if problems:
        for msg in problems:
            print(f"scheduler：启动核对不通过：{msg}", file=sys.stderr)
        return 2
    if a.once is not None:
        return asyncio.run(run.run(selected[0], dry_run=a.dry_run)).exit_code
    if a.dry_run:
        now = clock()
        for job in selected:
            doc = {"event": "plan", "job": job.name, "schedule": job.schedule.text, "identity": job.identity.name, "module": job.module,
                   "args": list(job.args), "env": sorted(child_env(job, env)), "timeout_seconds": job.timeout_seconds, "catch_up": job.catch_up,
                   "next": [t.isoformat() for t in upcoming(job.schedule, now, 3)]}
            out.write(json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        return 0
    out.write(json.dumps({"event": "start", "level": "info", "jobs": sorted(cfg.jobs), "synthetic": cfg.synthetic, "signed": cfg.signed},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    out.flush()
    asyncio.run(serve_forever(Loop(cfg.jobs, run, clock)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
