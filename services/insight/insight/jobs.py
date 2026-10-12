"""服务内接口（不对外开放，不在契约里）：判断生成、中圈指标重算、到期扫描、首份报告；SIE-03 的异议结案、对账卡生成、
触发规则评估与下发，以及调度器的预测冻结与删除执行（scheduled.py，用调度器自己的连接）；冲刺 5-H 的每周周报（weekly_reports）。

首份报告的对外形态（SIE-02b 第二步，lessons 待人确认 H 按默认）：契约草稿追加的工具 insight.report（service/tools.py）
经 report_inputs + build_first_report 取同一份结构化报告与两道检查；first_report 保留给服务内调用与测试。
触发时机（每次掌握重算后、每周、每天）由编排层与定时任务决定；命令行入口 `python -m insight.jobs sync|sweep|metrics`
（jobs_cli.py，只连本机数据库，不接生产调度，由人接调度）。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Mapping, Sequence

from .claims import MasteryRow
from .policy import Policy
from .predict import DbMasterySource
from .report import ReportClaim, ReportResult, build_first_report
from .scheduled import execute_due_erasures, freeze_due_predictions  # noqa: F401  调度器入口（starbuddy_scheduler 的连接）
from .shared import Forbidden, consistency, forbidden_matcher
from .sie03_policy import Sie03Policy
from .store import InsightStore, SyncResult
from .store_sie03 import ReconcileOutcome, SettleResult, Sie03Store
from .triggers import Suggestion
from .profile_pages import ProfilePageTrigger
from .store_weekly import WeeklyRun, run_week
from .weekly import WeeklyPolicy


async def sync_mastery(store: InsightStore, student: str, rows: Sequence[MasteryRow], policy: Policy) -> SyncResult:
    return await store.sync_mastery_claims(student, rows, policy)


async def sync_mastery_from_db(store: InsightStore, student: str, policy: Policy) -> SyncResult:
    """判断生成接真实掌握（KGE-04 第二步 PR 3）：读该学生全部当前节点键掌握行（DbMasterySource，只认当前地图里的单元）后
    走同一个 sync_mastery——判断的生成、流转与“我其实会”的结案（i_know_outcome）都在这里发生。
    触发时机（学习服务重算之后、每天兜底）由编排层与定时任务决定；读掌握行与写判断不在同一事务里，读到的行若在写之前被更正，
    current_basis 核对会按“没有依据”处理（不写），下一次触发补上"""
    rows = await DbMasterySource(store.pool).all_rows(student)
    return await store.sync_mastery_claims(student, rows, policy)


async def recompute_metrics(store: InsightStore, student: str, policy: Policy, forbidden: Forbidden | None = None):
    return await store.recompute_metrics(student, policy, forbidden or forbidden_matcher())


async def sweep(store: InsightStore, limit: int = 1000) -> int:
    return await store.sweep_expired(limit)


async def report_inputs(store: InsightStore, student: str, audience: str) -> tuple[list[ReportClaim], datetime]:
    """首份报告的输入：数据库当前时间与该受众可见的节点判断（store.report_claims，与 0010 前端读取同一组条件）"""
    async with store.pool.acquire() as conn:
        now = await conn.fetchval("SELECT now()")
    return await store.report_claims(student, audience), now


async def first_report(store: InsightStore, student: str, audience: str, policy: Policy,
                       check_cfg: "consistency.CheckConfig", forbidden: Forbidden | None = None) -> ReportResult:
    """首份画像报告（学生版 / 家长版）。调用方（BFF）必须先按档案授权确认当前登录者是学生本人或有效绑定家长（规则 5）；
    这里按判断可见范围过滤，并跑一致性与禁用推断检查，任一不过返回阻断编码、不出报告。
    对外经契约工具 insight.report（洞察服务另按 actor 再核对授权，0044）"""
    claims, now = await report_inputs(store, student, audience)
    return build_first_report(claims, audience, policy, check_cfg, forbidden or forbidden_matcher(), now)


# ---------- SIE-03（specs/sie-03-dispute-reconcile，PR②） ----------
async def settle_fact_corrections(ops: Sie03Store, student: str, rows: Sequence[MasteryRow] | None, policy: Policy) -> SettleResult:
    """“数据有误”：原始事实已更正的异议 → 相关判断撤回、异议结案、按判断生成重算（rows = 更正后的当前掌握行）。
    触发时机：原录入通道更正事实之后（考试记录替换、判分改判），以及每天的兜底扫描"""
    return await ops.settle_fact_corrections(student, rows, policy)


async def confirm_data(ops: Sie03Store, student: str, dispute_id: str) -> None:
    """复核结论“数据无误”（0023 复核队列按“转洞察服务”结案后）：异议维持结案，判断不变"""
    await ops.confirm_data(student, dispute_id)


async def sweep_disputes(ops: Sie03Store, sie03: Sie03Policy) -> dict[str, int]:
    return await ops.sweep_disputes(sie03)


async def dispute_metrics(ops: Sie03Store, since: datetime) -> dict[str, Any]:
    return await ops.dispute_metrics(since)


async def reconcile_exam(ops: Sie03Store, student: str, score_record_id: str, sie03: Sie03Policy, forbidden: Forbidden | None = None,
                         node_results: Mapping[str, float] | None = None, directions: Sequence[str] | None = None) -> ReconcileOutcome:
    """成绩（按考试安排录入）上传后生成对账卡；insight.reconcile 只读取结果。node_results / directions 来自上传解析（KGE-05），
    没有时偏差节点为空、低于区间默认“此前诊断偏乐观”，高于区间不出卡（待人确认）"""
    return await ops.reconcile_exam(student, score_record_id, sie03, forbidden or forbidden_matcher(), node_results, directions)


async def evaluate_triggers(ops: Sie03Store, student: str, sie03: Sie03Policy) -> list[tuple[str, Suggestion]]:
    """触发规则 v1：只写建议（suggested），给编排层与今日计划读（0033 授学习服务只读）"""
    return await ops.evaluate_triggers(student, sie03)


async def dispatch_triggers(ops: Sie03Store, student: str, sie03: Sie03Policy, result_refs: Mapping[str, str] | None = None) -> int:
    """下发建议：配置未签字一律拒绝（store_sie03.Unavailable），库里再核签字登记"""
    return await ops.dispatch_triggers(student, sie03, result_refs)


# ---------- 冲刺 5-H 跨科周报 v0（specs/trust-ui-sprint5 设计要点 4；迁移 0036） ----------
async def weekly_reports(store: InsightStore, week_start: date, policy: Policy, weekly: WeeklyPolicy, check_cfg: "consistency.CheckConfig",
                         forbidden: Forbidden | None = None, students: Sequence[str] | None = None, env: str | None = None,
                         profile_pages: "ProfilePageTrigger | None" = None) -> WeeklyRun:
    """每周定时任务（建议每周一凌晨跑上一周）：按学生生成学生版成长手记与家长版周报（固定结构 + 衔接短语占位 + 判断渲染句，
    每句带 claim_id；客观栏标来源 objective），两道检查（陈述—判断一致性、禁用推断）任一不过即 withheld；没有可见判断的不写；
    同一周重复运行不重写。生成完按配置抽检（默认 10%、至少 5 份）进统一复核队列，抽检不阻断。
    students 缺省为“有对学生或家长可见的节点判断的全部学生”。只接受已结束的周。env 是服务的运行环境：周报配置未签字时 test 以外
    抛 Unavailable（没传按非 test）。某个学生某个受众被库里守卫拒收时记 failed 并继续。只在壳内展示（待人确认 #5），不发短信、推送。
    profile_pages（KGE-04 第二步 PR 4，待人确认 #6 默认）：每个学生生成前先调学习服务 ensure 编译学情档案页（trigger = weekly，
    insight 令牌）；由 profile_pages.learning_profile_pages(settings.learning) 构造，没配学习服务时为 None。失败不阻塞周报，只计数（WeeklyRun.profile_pages）"""
    return await run_week(store, week_start, policy, weekly, check_cfg, forbidden or forbidden_matcher(), students, env, profile_pages)


if __name__ == "__main__":  # python -m insight.jobs sync|sweep|metrics|weekly（只连本机数据库；见 jobs_cli.py）
    import sys

    from .jobs_cli import main
    sys.exit(main())
