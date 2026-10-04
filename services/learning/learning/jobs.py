"""学习服务的一次性任务入口（不对外开放，不监听端口）。

  python -m learning.jobs mastery-sweep [--subject aya ...] [--now 2026-11-02T08:00:00+00:00]
  python -m learning.jobs admission-recompute --since 2026-11-02T08:00:00+00:00 [--subject aya ...]

admission-recompute（4-G 第二部分 PR D）：审核台执行题目准入变化（platform.change_admission）后，读 platform.admission_change_log(since)
逐题调 recompute_item（KGE-04 已有的按题重算，不改算法）；since 取审核台返回的 recompute_since。

掌握对账（KGE-04 第二步，specs/kge-04-mastery-step2/spec.md 第 2 节；learning/mastery/jobs.py）。以只继承 starbuddy_learning 的登录角色运行
（第一步待人确认 #7）；接入调度器等 ADR 03 / 10。环境变量：
  LEARNING_ENV                       dev | test（规则 9：不连 pilot 或生产）
  LEARNING_DATABASE_URL              asyncpg 连接串
  LEARNING_MASTERY_CONFIG_<SUBJECT>  该科掌握配置文件；未签字或合成配置只在 LEARNING_ENV=test 可加载（第一步 config.py）；
                                     文件里的 subject 必须与变量名的学科一致，否则直接报错退出
输出一行 JSON 计数（不含学生 ID 与证据内容）。--now 是注入时钟（只影响服务内的衰减与到期判断），只允许 LEARNING_ENV=test。
退出码：0 正常；1 有失败单元或 stale_estimate（已有记录的证据全部不再计入，待人确认 L）；2 用法或配置错误、没配任何学科、运行异常
（异常只输出错误类名，不输出消息：消息可能带出数据）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from typing import Mapping

import asyncpg

from .config import ALLOWED_ENVS, mastery_config_paths
from .mastery import load_config
from .mastery.jobs import admission_recompute, mastery_sweep
from .mastery.recompute import MasteryEngine


class JobError(Exception):
    """用法或配置错误：退出码 2。"""


def _parse(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m learning.jobs")
    sub = p.add_subparsers(dest="job", required=True)
    s = sub.add_parser("mastery-sweep", help="掌握对账：重算、衰减、复测任务缺补多撤、输出计数")
    s.add_argument("--subject", action="append", choices=("aya", "zhixing", "wenli", "echo"))
    s.add_argument("--now", help="注入时钟（带时区的 ISO 8601；只允许 LEARNING_ENV=test）")
    a = sub.add_parser("admission-recompute", help="准入变化后的按题重算：读 platform.admission_change_log，逐题调 recompute_item")
    a.add_argument("--since", required=True, help="起点（带时区的 ISO 8601；审核台执行准入后返回的 recompute_since）")
    a.add_argument("--subject", action="append", choices=("aya", "zhixing", "wenli", "echo"))
    a.add_argument("--now", help="注入时钟（带时区的 ISO 8601；只允许 LEARNING_ENV=test）")
    return p.parse_args(argv)


async def run(argv: list[str], environ: Mapping[str, str] | None = None, *, pool: asyncpg.Pool | None = None) -> dict:
    args = _parse(argv)
    e = dict(os.environ if environ is None else environ)
    mode = e.get("LEARNING_ENV", "")
    if mode not in ALLOWED_ENVS:
        raise JobError(f"LEARNING_ENV 必须是 {ALLOWED_ENVS} 之一")
    now = None
    if args.now:
        if mode != "test":
            raise JobError("--now 只允许 LEARNING_ENV=test")
        now = datetime.fromisoformat(args.now)
        if now.tzinfo is None:
            raise JobError("--now 必须带时区")
    since = None
    if args.job == "admission-recompute":
        try:
            since = datetime.fromisoformat(args.since)
        except ValueError:
            raise JobError("--since 必须是带时区的 ISO 8601 时间") from None
        if since.tzinfo is None:
            raise JobError("--since 必须带时区")
    configs = {}
    for subject, path in sorted(mastery_config_paths(e).items()):
        cfg = load_config(path, env=mode)
        if cfg.subject != subject:
            raise JobError(f"LEARNING_MASTERY_CONFIG_{subject.upper()} 指向的是另一科的配置")
        configs[subject] = cfg
    if not configs:
        raise JobError("没有配置任何学科的掌握配置（LEARNING_MASTERY_CONFIG_<SUBJECT>）：对账什么也不会做")
    own = pool is None
    if own:
        if not e.get("LEARNING_DATABASE_URL"):
            raise JobError("缺少 LEARNING_DATABASE_URL")
        pool = await asyncpg.create_pool(e["LEARNING_DATABASE_URL"], min_size=1, max_size=2)
    assert pool is not None
    try:
        engine = MasteryEngine(pool, configs)
        if args.job == "admission-recompute":
            counts = await admission_recompute(engine, since, subjects=args.subject, now=now)
            return {"job": "admission-recompute", "subjects": counts, "failed_units": len(engine.failed_units)}
        counts = await mastery_sweep(engine, subjects=args.subject, now=now)
        stale = sum(c.get("stale_estimate", 0) for c in counts.values())
        return {"job": "mastery-sweep", "subjects": counts, "failed_units": len(engine.failed_units), "stale_estimate": stale}
    finally:
        if own:
            await pool.close()


def exit_code(out: dict) -> int:
    return 1 if out["failed_units"] or out.get("stale_estimate") else 0


def main(argv: list[str] | None = None) -> int:
    try:
        out = asyncio.run(run(sys.argv[1:] if argv is None else argv))
    except JobError as e:
        print(f"mastery-sweep: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 — 只输出错误类名，不输出消息（可能带出数据）
        print(json.dumps({"job": "mastery-sweep", "error_class": type(e).__name__[:48]}), file=sys.stderr)
        return 2
    print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    return exit_code(out)


if __name__ == "__main__":
    sys.exit(main())
