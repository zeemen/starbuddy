"""计划完成 / 跳过的统计口径（PLT-15 PR2，specs/today-plan-snapshot-plt15 范围第 4、5 条）：中圈指标“计划完成率”（metrics.py）与
跨科周报客观栏的“自己标记完成 / 跳过的计划任务”（store_weekly.py）共用这一套，口径只有一份。

- 同一任务（plan_task_id）以最后一次记录为准：按 (发生时间, 记录 ID) 排序，后一条覆盖前一条（先完成后跳过算跳过，先跳过后完成算完成）；
- 计入哪些来源：配置 config/plan-tasks.json 的 count_sources。现在只实现 "all"（全部来源：计划、再练、复测、验证）；
  “是否只计 source = plan”待人确认（spec 待人确认 2）。只计 plan 要读今日计划快照表的来源列，洞察服务目前没有这张表的读权限
  （PR1 lessons 待人确认 4，需要一个授权迁移），所以配置写 "plan" 时拒绝加载，而不是悄悄按全部来源算；
- plan_done 只是学生自己点了完成，不是核实过的完成，更不是掌握（规则 6、11）：这里只数记录，不推断任何东西。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping

from .policy import CONFIG_DIR

PLAN_KINDS = ("plan_done", "plan_skip")
SUPPORTED_SOURCES = ("all",)
_KEYS = {"$comment", "schema_version", "count_sources"}


@dataclass(frozen=True)
class PlanCounting:
    count_sources: str  # all（只实现了这一种；plan 待人确认且需授权迁移）


def parse_plan_counting(doc: Mapping[str, object]) -> PlanCounting:
    if not isinstance(doc, Mapping) or set(doc) != _KEYS:
        raise ValueError("plan-tasks 配置的键不对")
    if doc["schema_version"] != "plan-tasks/0.1":
        raise ValueError("plan-tasks 配置的 schema_version 不认识")
    src = doc["count_sources"]
    if src not in SUPPORTED_SOURCES:
        raise ValueError(f"count_sources 只支持 {SUPPORTED_SOURCES}（只计 source = plan 待人确认，且需要快照表的读权限）")
    return PlanCounting(str(src))


def load_plan_counting(path: Path | None = None) -> PlanCounting:
    p = path or CONFIG_DIR / "plan-tasks.json"
    return parse_plan_counting(json.loads(p.read_text(encoding="utf-8")))


def default_counting() -> PlanCounting:
    """仓库里的口径配置：每次计数前现读（不缓存，改配置不必重启进程）；配置不合格时指标与周报一起失败。
    口径进版本：count_sources 改了必须同时升指标的 ALGORITHM_VERSION 与周报的 ALGORITHM_VERSION，并改下面的 COUNTING_VERSIONS
    （tests/test_plan_tasks.py 锁住三者对应，审查建议）"""
    return load_plan_counting()


# 口径与版本的对应：count_sources → (指标 metrics.ALGORITHM_VERSION, 周报 weekly.ALGORITHM_VERSION)
COUNTING_VERSIONS = {"all": ("0.1.0", "sie-weekly@0.1.1")}


@dataclass(frozen=True)
class PlanRecord:
    record_id: str          # insight.behavior_event.id
    kind: str               # plan_done / plan_skip
    plan_task_id: str
    occurred_at: datetime


def last_per_task(records: Iterable[PlanRecord], counting: PlanCounting | None = None) -> dict[str, str]:
    """同一任务以最后一次记录为准：{plan_task_id: plan_done | plan_skip}。别的事件类别与没有任务 ID 的记录不计。
    count_sources 现在只有 all（全部来源都计，不按来源过滤）"""
    if (counting or default_counting()).count_sources not in SUPPORTED_SOURCES:
        raise ValueError("count_sources 不支持")
    xs = sorted((r for r in records if r.kind in PLAN_KINDS and r.plan_task_id), key=lambda r: (r.occurred_at, r.record_id))
    last: dict[str, str] = {}
    for r in xs:
        last[r.plan_task_id] = r.kind
    return last


def plan_outcome_counts(records: Iterable[PlanRecord], counting: PlanCounting | None = None) -> tuple[int, int]:
    """(最后一次是完成的任务数, 最后一次是跳过的任务数)"""
    last = last_per_task(records, counting)
    done = sum(1 for k in last.values() if k == "plan_done")
    return done, len(last) - done
