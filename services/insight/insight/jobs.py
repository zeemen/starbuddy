"""服务内接口（不对外开放，不在契约里）：判断生成、中圈指标重算、到期扫描、首份报告。

第二步的 BFF 读接口经这里取首份报告的结构化内容（怎么接入——服务内 HTTP 路由还是契约新增工具——是契约变更，
登记在 lessons 的第二步计划里，由人决定）。触发时机（每次掌握重算后、每周、每天）由编排层与定时任务决定，
本步只提供函数。
"""
from __future__ import annotations

from typing import Sequence

from .claims import MasteryRow
from .policy import Policy
from .report import ReportResult, build_first_report
from .shared import Forbidden, consistency, forbidden_matcher
from .store import InsightStore, SyncResult


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
