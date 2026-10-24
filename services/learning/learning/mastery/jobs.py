"""掌握对账（KGE-04 第二步 PR 2；设计正文 specs/mastery-estimate-sprint4/spec.md 第 7 节“对账任务”）。

入口：python -m learning.jobs mastery-sweep（learning/jobs.py）。以 starbuddy_learning 身份一次性运行（第一步 #7、第二步 #11），
接入调度器等 ADR 03 / 10。每次运行、每个有可用配置的学科：
  ① 重算 sweep.lookback 内有新作答、新判分（含复核结论写出的更正判分）的学生的全部单元；
  ② 重算全部当前节点键记录所在的单元：按时间衰减更新置信度（有实质变化才写），排 decay 任务；
  ③ 同时核对任务：每条带 next_retest_at 的当前记录恰有一条开着的复测轮任务，缺补多撤（开着的重放任务所在单元也重算）；
  ④ 输出计数（不含学生 ID 与证据内容）。
小批量：每个单元一个事务（recompute）；时钟可注入（只在服务里，库里的 computed_at 仍是真实时间）。
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Iterable

from .. import processing
from ..store import learning_tx
from . import recompute as R
from .recompute import MasteryEngine, recompute_student


async def sweep_students(engine: MasteryEngine, subject: str, now: datetime) -> list[str]:
    cfg = engine.config(subject)
    assert cfg is not None
    since = now - timedelta(seconds=cfg["sweep"]["lookback_seconds"])
    # LT-06 决定记录（续）第 4 条：库层处理判据（迁移 0059）在就过滤——退出、删除、没有有效同意的学生不再重算、衰减、补复测任务
    gate = await processing.available(engine.pool)
    async with learning_tx(engine.pool) as conn:
        rows = await conn.fetch(
            ("SELECT u.s FROM (" if gate else "") +
            """SELECT student_id::text FROM platform.attempt
                WHERE subject = $1::platform.subject AND recorded_at >= $2::timestamptz
               UNION
               SELECT student_id::text FROM platform.grade_result
                WHERE subject = $1::platform.subject AND created_at >= $2::timestamptz
               UNION
               SELECT student_id::text FROM platform.mastery_estimate
                WHERE subject = $1::platform.subject AND is_current AND node_id IS NOT NULL
               UNION
               SELECT student_id::text FROM platform.retest_task
                WHERE subject = $1::platform.subject AND status = 'open' AND reason <> 'verify_self_report'"""
            + (") u(s) WHERE platform.student_processing_allowed(u.s::uuid) ORDER BY 1" if gate else " ORDER BY 1"), subject, since)
    return [r[0] for r in rows]


async def admission_recompute(engine: MasteryEngine, since: datetime, *, subjects: Iterable[str] | None = None,
                              now: datetime | None = None) -> dict[str, dict[str, int]]:
    """4-G 准入变化之后的按题重算（specs/release-gate-sprint4 第二部分 PR D；spec 0039 差异 #7）：
    读 platform.admission_change_log(since)（审核台执行 platform.change_admission 的记录，不含签字人），对每道变化的题
    调 recompute_item（KGE-04 已有的按题重算，算法不在这里）。同一道题多次变化只算一次；没有可用配置的学科只记 skipped_no_config。
    审核台的库身份不能写个人层，所以重算由学习服务身份在这里做；since 由审核台执行准入后返回（recompute_since）。"""
    allowed = set(subjects) if subjects is not None else None
    async with learning_tx(engine.pool) as conn:
        rows = await conn.fetch("SELECT DISTINCT subject::text, item_id FROM platform.admission_change_log($1) ORDER BY 1, 2", since)
    out: dict[str, Counter] = {}
    for subject, item_id in rows:
        if allowed is not None and subject not in allowed:
            continue
        c = out.setdefault(subject, Counter())
        c.update(await R.recompute_item(engine, subject, item_id, now=now))
        c["items"] += 1
    return {s: dict(sorted(c.items())) for s, c in sorted(out.items())}


async def mastery_sweep(engine: MasteryEngine, *, subjects: Iterable[str] | None = None,
                        now: datetime | None = None) -> dict[str, dict[str, int]]:
    """返回 {学科: 计数}。没有可用配置的学科只记 skipped_no_config。"""
    now = now or engine.clock()
    out: dict[str, dict[str, int]] = {}
    for subject in sorted(set(subjects) if subjects is not None else set(engine.configs)):
        if engine.config(subject) is None:
            out[subject] = {"skipped_no_config": 1}
            continue
        c: Counter = Counter()
        for student in await sweep_students(engine, subject, now):
            c.update(await recompute_student(engine, student, subject, now=now))
            c["students"] += 1
        out[subject] = dict(sorted(c.items()))
    return out
