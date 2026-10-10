"""今日计划跨科编排：取三个来源、汇总（PLT-11 spec 范围第 1 条）。

来源全部在进程内复用已有模块的读取函数（不走 HTTP）：
- plan：learning/plan.py 的 plan_today（各科适配器 /plan；某科 unavailable 即这一科的计划来源不可用）；
- practice：PracticeService.queue 的 due 条目（再练模块没启动、学科没开、读库失败 → 这一科的再练来源不可用）；
- retest：RetestService.queue 的 due 条目（同上）。
任一来源（按学科）未启用或失败只跳过它，不整体失败：未启用（模块没启动、这一科没开、没配适配器）标 source_disabled，
启用了但失败（含条目不合格）标 source_unavailable。各科计划只算一次（不传预算），原样附在响应的 plan 里：
BFF 开关打开时按科 sections 用它构造，不再另调 /internal/v1/plan（每页只排一次计划）；截断只在跨科汇总里做。
只读：三个读取函数都不写表；本模块不写任何表、不计量、不调模型。对 BFF 只回 merge.Item 的七个字段与截断计数、各来源状态；
日志只记学科数、各来源条数、截掉的条数与耗时，不记学生 ID 与条目内容（规则 8）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any

from ..config import Settings
from ..errors import IngestError
from ..plan import plan_today
from . import merge as M
from .config import BUDGET_RANGE, TodayConfig

log = logging.getLogger("starbuddy.learning.today")
SUBJECTS = ("wenli", "aya", "zhixing")         # 与 PlanQuery 相同（英语搁置）
STUDENT_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
OK = "ok"
UNAVAILABLE = "source_unavailable"             # 启用了但失败（读库出错、连接池忙、适配器不可用、条目不合格）
DISABLED = "source_disabled"                   # 没启用（模块没启动、配置里这一科没开、这一科没配适配器）：不算失败
RETEST_KINDS = ("retest", "verify")
Got = tuple[str, "list[M.Item] | None"]
QUERY_KEYS = frozenset({"request_id", "student_ref", "subjects"})
OPTIONAL_KEYS = frozenset({"time_budget_minutes"})


def _bad(msg: str) -> IngestError:
    return IngestError(400, "INVALID_REQUEST", msg)


def validate(body: Any) -> dict:
    """请求严格校验：未知键、学生 ID 不是小写 UUID、学科不在范围或重复、预算不在 [20, 40] 一律 400。"""
    if not isinstance(body, dict) or not QUERY_KEYS <= set(body) or set(body) - QUERY_KEYS - OPTIONAL_KEYS:
        raise _bad("unexpected or missing fields")
    rid = body["request_id"]
    try:
        ok = isinstance(rid, str) and str(uuid.UUID(rid)) == rid.lower()
    except ValueError:
        ok = False
    if not ok:
        raise _bad("request_id must be a UUID")
    if not isinstance(body["student_ref"], str) or not STUDENT_RE.fullmatch(body["student_ref"]):
        raise _bad("student_ref must be a lowercase UUID")
    subjects = body["subjects"]
    if (not isinstance(subjects, list) or not 1 <= len(subjects) <= len(SUBJECTS) or len(set(map(str, subjects))) != len(subjects)
            or not all(isinstance(s, str) and s in SUBJECTS for s in subjects)):
        raise _bad("subjects must be 1-3 distinct subjects")
    if "time_budget_minutes" in body:
        b = body["time_budget_minutes"]
        lo, hi = BUDGET_RANGE
        if not (isinstance(b, int) and not isinstance(b, bool) and lo <= b <= hi):
            raise _bad(f"time_budget_minutes must be an integer in [{lo}, {hi}]")
    return body


@dataclass
class TodayService:
    settings: Settings
    cfg: TodayConfig
    state: dict                       # app 的共享状态：practice、retest、plan（池与客户端）在运行时取

    async def _retest(self, student: str, subject: str) -> Got:
        svc = self.state.get("retest")
        if svc is None or subject not in svc.cfg.subjects_enabled or subject not in svc.practice.cfg.subjects_enabled:
            return DISABLED, None                 # 复测模块没启动、或这一科没开：未启用，不算失败
        try:
            out = await svc.queue(student, subject)
            minutes = self.cfg.entry_minutes
            items = []
            for e in out["entries"]:
                if e["status"] != "due":
                    continue
                if e["kind"] not in RETEST_KINDS:
                    raise ValueError("unknown retest entry kind")
                items.append(M.Item(subject, e["kind"], e["node_id"], minutes[e["kind"]], "retest", e["entry_id"], None))
            return OK, items
        except Exception:  # noqa: BLE001 来源失败（连接池忙、读库出错、条目不合格）：只跳过这一来源
            return UNAVAILABLE, None

    async def _practice(self, student: str, subject: str) -> Got:
        svc = self.state.get("practice")
        if svc is None or subject not in svc.cfg.subjects_enabled:
            return DISABLED, None
        try:
            out = await svc.queue(student, subject)
            minutes = self.cfg.entry_minutes["practice"]
            return OK, [M.Item(subject, "practice", e["node_id"], minutes, "practice", e["entry_id"], None)
                        for e in out["entries"] if e["status"] == "due"]
        except Exception:  # noqa: BLE001 同上
            return UNAVAILABLE, None

    async def _plan(self, student: str, subjects: list[str]) -> tuple[dict | None, dict[str, Got]]:
        """各科计划：只调一次 plan_today（不传预算，与 /internal/v1/plan 同一请求形状：BFF 开关打开时按科 sections 也用这一份）。
        返回（PlanDigest 或 None, 学科 → 来源结果）。这一科没配适配器 = 未启用；配了但 unavailable、或整体失败 = 不可用。"""
        out: dict[str, Got] = {s: (DISABLED, None) if s not in self.settings.adapters else (UNAVAILABLE, None) for s in subjects}
        plan = self.state.get("plan")
        if plan is None:
            return None, out
        pool, client = plan
        query = {"request_id": str(uuid.uuid4()), "student_ref": student, "subjects": list(subjects)}
        try:
            digest, _ = await plan_today(self.settings, pool, client, query)
            for sec in digest["subjects"]:
                if sec["status"] == "ok":
                    out[sec["subject"]] = (OK, [M.Item(sec["subject"], t["kind"], (t["target_node_ids"] or [None])[0],
                                                       int(t["estimated_minutes"]), "plan", None, t["rationale_code"])
                                                for t in sec["tasks"]])
            return digest, out
        except Exception:  # noqa: BLE001 计划整体失败：各科的计划来源都不可用（BFF 按科也显示“暂无安排”）
            return None, {s: (DISABLED, None) if s not in self.settings.adapters else (UNAVAILABLE, None) for s in subjects}

    async def today(self, body: Any) -> dict:
        q = validate(body)
        student, subjects = q["student_ref"], list(q["subjects"])
        budget = q.get("time_budget_minutes", self.cfg.default_budget_minutes)
        rt, pr, (digest, pl) = await asyncio.gather(
            asyncio.gather(*(self._retest(student, s) for s in subjects)),
            asyncio.gather(*(self._practice(student, s) for s in subjects)),
            self._plan(student, subjects))
        by_source: dict[str, dict[str, Got]] = {"retest": dict(zip(subjects, rt)), "practice": dict(zip(subjects, pr)), "plan": pl}
        sources = [{"source": src, "subject": s, "status": by_source[src][s][0]} for src in ("retest", "practice", "plan") for s in subjects]

        def flat(src: str) -> list[M.Item]:
            return [i for s in subjects for i in (by_source[src][s][1] or [])]
        items, cut = M.build(flat("retest"), flat("practice"), flat("plan"), priority=self.cfg.source_priority,
                             subjects=subjects, budget=budget)
        log.info(json.dumps({"event": "today", "subjects": len(subjects), "items": len(items), "truncated": cut,
                             "unavailable": sum(1 for x in sources if x["status"] == UNAVAILABLE)}, ensure_ascii=False))
        return {"items": [i.view() for i in items], "truncated_count": cut, "budget_minutes": budget, "sources": sources,
                "plan": digest}
