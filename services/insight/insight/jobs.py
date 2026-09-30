"""服务内接口（不对外开放，不在契约里）：判断生成、中圈指标重算、到期扫描、首份报告；SIE-03 的异议结案、对账卡生成、
触发规则评估与下发，以及调度器的预测冻结与删除执行（scheduled.py，用调度器自己的连接）。

第二步的 BFF 读接口经这里取首份报告的结构化内容（怎么接入——服务内 HTTP 路由还是契约新增工具——是契约变更，
登记在 lessons 的第二步计划里，由人决定）。触发时机（每次掌握重算后、每周、每天）由编排层与定时任务决定，
本步只提供函数。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from .claims import MasteryRow
from .policy import Policy
from .report import ReportResult, build_first_report
from .scheduled import execute_due_erasures, freeze_due_predictions  # noqa: F401  调度器入口（starbuddy_scheduler 的连接）
from .shared import Forbidden, consistency, forbidden_matcher
from .sie03_policy import Sie03Policy
from .store import InsightStore, SyncResult
from .store_sie03 import ReconcileOutcome, SettleResult, Sie03Store
from .triggers import Suggestion


async def sync_mastery(store: InsightStore, student: str, rows: Sequence[MasteryRow], policy: Policy) -> SyncResult:
    return await store.sync_mastery_claims(student, rows, policy)


async def recompute_metrics(store: InsightStore, student: str, policy: Policy, forbidden: Forbidden | None = None):
    return await store.recompute_metrics(student, policy, forbidden or forbidden_matcher())


async def sweep(store: InsightStore, limit: int = 1000) -> int:
    return await store.sweep_expired(limit)


async def first_report(store: InsightStore, student: str, audience: str, policy: Policy,
                       check_cfg: "consistency.CheckConfig", forbidden: Forbidden | None = None) -> ReportResult:
    """首份画像报告（学生版 / 家长版）。调用方（BFF）必须先按档案授权确认当前登录者是学生本人或有效绑定家长（规则 5）；
    这里按判断可见范围过滤，并跑一致性与禁用推断检查，任一不过返回阻断编码、不出报告"""
    async with store.pool.acquire() as conn:
        now = await conn.fetchval("SELECT now()")
    claims = await store.report_claims(student, audience)
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
