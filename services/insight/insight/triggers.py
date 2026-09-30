"""触发规则 v1（SIE V1.1 第七章；spec 设计要点 3；迁移 0033）：纯函数，不连库、不读系统时钟。

输出只是给编排层与今日计划的“动作建议”（intervention_log.status = suggested）；每条引用判断账本里同一学生、未过期、
成立或假设的判断（规则 11；0033 在写入与下发时各核一次）。参数全部来自 SIE-03 配置（未签字的合成占位），下发要求配置已签字、
且库里 insight.intervention_rule_signoff 有同一规则与参数版本的签字登记——本模块只生成建议。
- 提醒 reminder.review_due：知识节点判断在 review_due_within_days 天内到期（到期即复测）→ plan_review_node（放进今日计划，不单独推送）；
  “验证题待做”（plan_verification_item）：被“我其实会”异议的判断已是待验证，0033 不许动作引用待验证判断，v1 不生成（lessons）；
- 证据化鼓励 encourage.progress：encourage_lookback_days 天内由“假设 → 成立”（evidence_threshold）或“待验证 → 成立”
  （verification_supported）、现在仍成立且未过期的知识节点判断 → encourage_with_evidence（只基于具体进步，不做空泛表扬）；
- 降负 reduce_load.break / reduce_load.exit_after_errors：连续中断 ≥ break_days 天（此前有活动），或最近窗口内“连错后退出”次数
  ≥ 下限且多于上一个窗口 → 缩短任务、插入熟悉题、询问原因（不追加任务）；引用配置里的中圈指标判断（持续性、计划完成率），
  没有可引用的判断就不生成（判断先于展示）。
- 去重：同一规则、动作、判断集合在 dedup_days 天内已有记录的不再生成。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from .claims import ClaimRow
from .sie03_policy import TriggerParams

LIVE_OK = ("hypothesis", "established")
MAX_REFS = 16  # 0033 claim_ids 1–16
REDUCE_ACTIONS = ("shorten_task", "insert_familiar_item", "ask_reason")


@dataclass(frozen=True)
class Transition:
    claim_id: str
    to_status: str
    reason: str
    at: datetime


@dataclass(frozen=True)
class LoggedAction:
    rule_key: str
    action: str
    claim_ids: frozenset[str]
    created_at: datetime


@dataclass(frozen=True)
class TriggerFacts:
    claims: tuple[ClaimRow, ...]                    # 该学生的判断（任意状态；本模块自己过滤）
    transitions: tuple[Transition, ...] = ()        # 判断历史里的流转
    activity: tuple[datetime, ...] = ()             # 会话开始与作答时间
    exits_after_errors: tuple[datetime, ...] = ()   # 行为事件 exit_after_errors
    logged: tuple[LoggedAction, ...] = ()           # 已有的动作记录（去重）


@dataclass(frozen=True)
class Suggestion:
    rule_key: str
    action: str
    claim_ids: tuple[str, ...]


def _usable(c: ClaimRow, now: datetime) -> bool:
    return c.status in LIVE_OK and c.expires_at > now


def evaluate(facts: TriggerFacts, params: TriggerParams, now: datetime) -> list[Suggestion]:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    usable = {c.id: c for c in facts.claims if _usable(c, now)}
    node_claims = sorted((c for c in usable.values() if c.target_type == "node"), key=lambda c: (c.expires_at, c.id))
    out: list[Suggestion] = []

    # 提醒：即将到期（到期即复测）
    horizon = now + timedelta(days=params.review_due_within_days)
    for c in node_claims:
        if c.expires_at <= horizon:
            out.append(Suggestion("reminder.review_due", "plan_review_node", (c.id,)))

    # 证据化鼓励：窗口内升为成立、现在仍成立
    since = now - timedelta(days=params.encourage_lookback_days)
    rising = sorted({t.claim_id: t for t in facts.transitions
                     if t.to_status == "established" and t.reason in ("evidence_threshold", "verification_supported")
                     and since < t.at <= now}.values(), key=lambda t: (-t.at.timestamp(), t.claim_id))
    enc = [t.claim_id for t in rising if t.claim_id in usable and usable[t.claim_id].status == "established"
           and usable[t.claim_id].target_type == "node"][:MAX_REFS]
    if enc:
        out.append(Suggestion("encourage.progress", "encourage_with_evidence", tuple(sorted(enc))))

    # 降负：引用中圈指标判断
    metric_refs = tuple(sorted(c.id for c in usable.values()
                               if c.target_type == "metric" and c.target_id.split(".", 1)[0] in params.reduce_load_metric_keys))[:MAX_REFS]
    if metric_refs:
        acts = sorted(a for a in facts.activity if a <= now)
        if acts and acts[-1] <= now - timedelta(days=params.break_days) and len(acts) >= 1:
            out.extend(Suggestion("reduce_load.break", a, metric_refs) for a in REDUCE_ACTIONS)
        w = timedelta(days=params.exit_after_errors_window_days)
        recent = sum(1 for x in facts.exits_after_errors if now - w < x <= now)
        before = sum(1 for x in facts.exits_after_errors if now - 2 * w < x <= now - w)
        if recent >= params.exit_after_errors_min and recent > before:
            out.extend(Suggestion("reduce_load.exit_after_errors", a, metric_refs) for a in REDUCE_ACTIONS)

    dedup_since = now - timedelta(days=params.dedup_days)
    seen = {(l.rule_key, l.action, l.claim_ids) for l in facts.logged if l.created_at > dedup_since}
    result = []
    for s in out:
        key = (s.rule_key, s.action, frozenset(s.claim_ids))
        if key not in seen:
            seen.add(key)
            result.append(s)
    return result


def check_suggestions(suggestions: Sequence[Suggestion], claims: Sequence[ClaimRow], student: str, now: datetime) -> None:
    """写入前再核一次（0033 守卫同一条件）：每个引用都是同一学生、未过期、成立或假设的判断"""
    ok = {c.id for c in claims if c.student_id == student and _usable(c, now)}
    for s in suggestions:
        if not 1 <= len(s.claim_ids) <= MAX_REFS or not set(s.claim_ids) <= ok:
            raise ValueError("suggestion references a claim that is not usable")
