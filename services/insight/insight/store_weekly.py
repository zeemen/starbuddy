"""跨科周报 v0 的数据库层（迁移 0036）：只以 starbuddy_insight 连接（与 store.InsightStore 同一个连接池、同一套角色断言）。

- 读判断：沿用 InsightStore.visible_claims（与 0010 前端策略同一条件：可见范围含受众、非终态、未过期、模板已审核；中圈指标对学生家长不返回），
  只取知识节点、假设或成立；weekly.build_weekly 里再按首发学科、模板版本、来源过滤一次；
- 读使用记录：只用已授的列（0025 insight.behavior_event 的会话结束时长与计划事件，0028 platform.attempt 的学科、用时），
  不读证据原文；行为事件按类别读，不按来源过滤（0057 起计划事件来源是 self、会话事件是 objective）；
- 写：insight.weekly_report 只追加（同一学生、同一周、同一受众只有一份，重复运行 ON CONFLICT DO NOTHING）；库里的守卫再核每句判断；
- 抽检：platform.review_sample_weekly_reports（只授洞察服务），按周抽样进统一复核队列，不阻断周报可见。
时间一律取数据库的 now()。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Sequence

import asyncpg

from .plan_tasks import PLAN_KINDS, PlanRecord, plan_outcome_counts
from .policy import TEST_ENV, Policy
from .profile_pages import SUBJECTS as PROFILE_SUBJECTS
from .profile_pages import ProfilePageTrigger
from .shared import Forbidden, consistency
from .store import InsightStore
from .store_sie03 import Unavailable
from .weekly import (ALGORITHM_VERSION, AUDIENCES, ObjectiveFacts, WeeklyClaim, WeeklyPolicy, WeeklyResult, build_weekly, sentences_of,
                     week_window)

LOOKBACK_DAYS = 35  # 与 platform.review_sample_weekly_reports 的可抽范围一致


@dataclass(frozen=True)
class WeeklyOutcome:
    student_id: str
    audience: str
    status: str            # generated / withheld / empty（没有可见判断，不写）/ exists（这一周已有，不重写）/ failed（库里守卫拒收，记下后继续）
    report_id: str | None = None
    blocked: tuple[str, ...] = ()


@dataclass(frozen=True)
class WeeklyRun:
    week_start: date
    outcomes: tuple[WeeklyOutcome, ...]
    sampled: int
    # 周报前触发学情档案页编译的结果计数（KGE-04 第二步 PR 4）：ok / failed；没配学习服务时两者都是 0
    profile_pages: tuple[tuple[str, int], ...] = (("ok", 0), ("failed", 0))

    def count(self, status: str) -> int:
        return sum(1 for o in self.outcomes if o.status == status)


async def weekly_claims(store: InsightStore, student: str, audience: str) -> list[WeeklyClaim]:
    out = []
    for v in await store.visible_claims(student, audience, target_type="node", statuses=("hypothesis", "established")):
        c = v.row
        out.append(WeeklyClaim(v.for_render(), c.subject, c.target_type, c.visibility, c.expires_at, c.source_type, c.created_at,
                               c.updated_at))
    return out


async def objective_facts(conn: asyncpg.Connection, student: str, week_start: date) -> ObjectiveFacts:
    start, end = week_window(week_start)
    beh = await conn.fetch(
        """SELECT id, kind, payload ->> 'plan_task_id' AS task, payload ->> 'duration_sec' AS sec, occurred_at FROM insight.behavior_event
            WHERE student_id = $1::uuid AND kind IN ('session_end', 'plan_done', 'plan_skip') AND occurred_at >= $2 AND occurred_at < $3
            ORDER BY occurred_at, id""", student, start, end)
    seconds = sum(int(r["sec"]) for r in beh if r["kind"] == "session_end" and r["sec"] is not None)
    # 与计划完成率同一口径（plan_tasks.py，PLT-15 PR2）：同一任务以本周内最后一次记录为准；计入全部来源（计划、复测、再练、验证，用户决定）。
    # 这是学生自己标记的记录（来源 self，0057），周报里放在单独的 self_marked 栏（weekly.objective_column）
    done, skipped = plan_outcome_counts(PlanRecord(str(r["id"]), r["kind"], r["task"], r["occurred_at"]) for r in beh
                                        if r["kind"] in PLAN_KINDS and r["task"])
    rows = await conn.fetch(
        """SELECT subject::text AS subject, coalesce(sum(elapsed_sec), 0) AS seconds, count(*) AS n FROM platform.attempt
            WHERE student_id = $1::uuid AND occurred_at >= $2 AND occurred_at < $3 GROUP BY subject ORDER BY subject""",
        student, start, end)
    return ObjectiveFacts(seconds, done, skipped, {r["subject"]: (int(r["seconds"]), int(r["n"])) for r in rows})


async def students_with_claims(store: InsightStore) -> list[str]:
    """有对学生或家长可见的非终态节点判断的学生（周报的候选）"""
    async with store.pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT DISTINCT c.student_id::text AS s FROM insight.claim c
                WHERE c.target_type = 'node' AND c.status IN ('hypothesis', 'established') AND c.expires_at > now()
                  AND c.visibility && ARRAY['student', 'guardian'] ORDER BY 1""")
    return [r["s"] for r in rows]


async def insert_weekly(conn: asyncpg.Connection, student: str, week_start: date, audience: str, result: WeeklyResult,
                        policy_version: str) -> str | None:
    """写一份周报；这一周这个受众已经有了就返回 None（快照只追加，不重写）"""
    if result.ok:
        r = result.report
        assert r is not None
        args = ("generated", json.dumps(sentences_of(r), ensure_ascii=False), json.dumps(r["objective"], ensure_ascii=False), [])
    else:
        args = ("withheld", "[]", None, list(result.blocked))
    rid = await conn.fetchval(
        """INSERT INTO insight.weekly_report (student_id, week_start, week_end, audience, status, sentences, objective, withheld_reasons,
                                              algorithm_version, policy_version)
           VALUES ($1::uuid, $2::date, $2::date + 6, $3, $4, $5::text::jsonb, $6::text::jsonb, $7::text[], $8, $9)
           ON CONFLICT (student_id, week_start, audience) DO NOTHING RETURNING id""",
        student, week_start, audience, *args, ALGORITHM_VERSION, policy_version)
    return None if rid is None else str(rid)


async def generate_for_student(store: InsightStore, student: str, week_start: date, policy: Policy, weekly: WeeklyPolicy,
                               check_cfg: "consistency.CheckConfig", forbidden: Forbidden) -> list[WeeklyOutcome]:
    out: list[WeeklyOutcome] = []
    async with store.pool.acquire() as conn:
        now = await conn.fetchval("SELECT now()")
        facts = await objective_facts(conn, student, week_start)
    for audience in AUDIENCES:
        claims = await weekly_claims(store, student, audience)
        res = build_weekly(claims, audience, week_start, policy, weekly, check_cfg, forbidden, now, facts)
        if res.empty:
            out.append(WeeklyOutcome(student, audience, "empty"))
            continue
        try:
            async with store.pool.acquire() as conn:
                rid = await insert_weekly(conn, student, week_start, audience, res, f"{policy.config_version}_{weekly.config_version}")
        except asyncpg.CheckViolationError:
            # 库里的守卫拒收（例如渲染之后判断又被修订、可见范围变了）：记 failed，不影响其他学生与受众（契约审查 L4）
            out.append(WeeklyOutcome(student, audience, "failed", None, ("db_check_violation",)))
            continue
        status = "exists" if rid is None else ("generated" if res.ok else "withheld")
        out.append(WeeklyOutcome(student, audience, status, rid, res.blocked))
    return out


async def sample_week(store: InsightStore, week_start: date, weekly: WeeklyPolicy) -> int:
    async with store.pool.acquire() as conn:
        return int(await conn.fetchval("SELECT platform.review_sample_weekly_reports($1::date, $2::numeric, $3::integer)",
                                       week_start, weekly.sample_ratio, weekly.sample_min))


async def check_week(store: InsightStore, week_start: date) -> None:
    """只接受已结束（周一 + 7 天 ≤ 今天，上海时区）、35 天内的周一（与 0036 守卫、抽样函数同一范围）"""
    if week_start.isoweekday() != 1:
        raise ValueError("week_start must be a Monday")
    async with store.pool.acquire() as conn:
        today = await conn.fetchval("SELECT (now() AT TIME ZONE 'Asia/Shanghai')::date")
    if week_start + timedelta(days=7) > today or (today - week_start).days > LOOKBACK_DAYS:
        raise ValueError("week_start must be a finished week within the last 35 days")


async def ensure_profile_pages(profile_pages: "ProfilePageTrigger | None", student: str, policy: Policy) -> tuple[int, int]:
    """周报生成前按周报可见学科触发学习服务编译档案页（第二步待人确认 #6 默认）；失败不阻塞周报，只计数。返回 (成功, 失败)"""
    if profile_pages is None:
        return 0, 0
    ok = failed = 0
    for subject in sorted(s for s in policy.visible_subjects if s in PROFILE_SUBJECTS):
        try:
            done = await profile_pages.ensure(student, subject)
        except Exception:  # noqa: BLE001 — 触发失败不影响周报
            done = False
        ok, failed = (ok + 1, failed) if done else (ok, failed + 1)
    return ok, failed


async def run_week(store: InsightStore, week_start: date, policy: Policy, weekly: WeeklyPolicy, check_cfg: "consistency.CheckConfig",
                   forbidden: Forbidden, students: Sequence[str] | None = None, env: str | None = None,
                   profile_pages: "ProfilePageTrigger | None" = None) -> WeeklyRun:
    """env：服务的运行环境（INSIGHT_ENV，由调用方传入）。周报配置（标题、衔接短语、排除词、抽检比例）未签字时，
    test 以外一律不生成（安全审查 F2）；没传 env 按非 test 处理（fail closed）。
    profile_pages：每个学生生成周报前先触发学情档案页编译（KGE-04 第二步 PR 4），失败不阻塞周报"""
    if env != TEST_ENV and not weekly.signed:
        raise Unavailable("weekly policy is not signed")
    await check_week(store, week_start)
    targets = list(students) if students is not None else await students_with_claims(store)
    outcomes: list[WeeklyOutcome] = []
    ok = failed = 0
    for s in targets:
        o, f = await ensure_profile_pages(profile_pages, s, policy)
        ok, failed = ok + o, failed + f
        outcomes.extend(await generate_for_student(store, s, week_start, policy, weekly, check_cfg, forbidden))
    sampled = await sample_week(store, week_start, weekly)
    return WeeklyRun(week_start, tuple(outcomes), sampled, (("ok", ok), ("failed", failed)))


