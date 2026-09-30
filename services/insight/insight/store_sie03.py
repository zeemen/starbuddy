"""SIE-03 的数据库层（迁移 0032–0034 授给 starbuddy_insight 的表与函数；不写任何 platform 表）。

与 store.InsightStore 同一个连接池、同一把学生锁（insight.claims:<学生>），时间一律取数据库 now()。每个入口一个事务：
- 异议（0032）：insight.raise_dispute（属主 authz，按库里关系再核提出人与判断可见性）→ 核对库里记下的提出人身份与请求 actor 一致
  （不一致整笔回滚，按“不存在”处理）→ i_know 同一事务把判断转待验证（0010，原因 dispute）并登记验证任务引用（延迟约束在提交时核对）；
  one_off_slip 只记异议行（学生自述）；data_error 带原始事实引用进 0023 复核队列。
  事实更正（exam_record 被替换、作答判分被改判）后：相关判断撤回（fact_corrected）、异议以 fact_corrected 结案、同一事务里按判断生成重算。
- 预测（0034）：找学生 / 家长录入的考试安排（insight.exam_record，raw_score 为空）→ 合成掌握行估区间 → 写考试类判断与预测；
  考试当天零点（上海）起不再新建或修改（库层守卫），冻结由定时任务（scheduled.py）做。
- 对账（0034）：成绩（按安排录入）+ 已冻结预测 → 区间内外、偏差节点与方向、平台调整（偏差节点的判断转待验证，原因 exam_deviation）
  → 写对账判断与对账卡（每句只存判断 ID 与模板键）。
- 触发规则（0033）：读判断、历史、活动与已有记录 → triggers.evaluate → 只写建议；下发要求配置已签字（库里再核签字登记）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Awaitable, Callable, Mapping, Sequence

import asyncpg

from .claims import MAX_EVIDENCE, ClaimRow, MasteryRow
from .dispute import KINDS, NEXT_STEP, VerificationScheduler, fact_ref, stub_task_refs, verify_by
from .policy import Policy
from .predict import (EXAM_TYPES, MODEL_VERSION, RECONCILE_MODEL_VERSION, PredictInputError, SentenceClaim, check_sentences,
                      predict_interval, reconcile_plan)
from .render import render_text
from .shared import Forbidden
from .sie03_policy import AUDIENCE_ORDER, Sie03Policy
from .store import _CLAIM_COLS, InsightStore, SyncResult, claim_row
from .triggers import LoggedAction, Suggestion, Transition, TriggerFacts, check_suggestions, evaluate

LOOKBACK_DAYS = 180
TERMINAL = ("refuted", "withdrawn")


class StoreError(Exception):
    """SIE-03 入口的业务错误（不带数据）"""
    kind = "invalid"


class NotFound(StoreError):
    kind = "not_found"


class Invalid(StoreError):
    kind = "invalid"


class Conflict(StoreError):
    kind = "conflict"


class RateLimited(StoreError):
    kind = "rate_limited"


class Unavailable(StoreError):
    """配置或依赖缺位（没有已审核模板、没有签字等）"""
    kind = "unavailable"


@dataclass(frozen=True)
class DisputeOutcome:
    dispute_id: str
    next_step: str
    verification_item_refs: tuple[str, ...] = ()
    verify_by: datetime | None = None
    # 契约要求 open_fact_for_correction 时带 fact_ref；它是原始事实的内部引用，只供 BFF 打开原录入通道，BFF 不得转发给前端
    # 或第三方（安全审查 F5，lessons 登记）
    fact_ref: str | None = None
    evidence_ids: tuple[str, ...] = ()  # 被异议判断的证据（契约审查 8）


@dataclass(frozen=True)
class PredictOutcome:
    prediction_id: str
    claim_id: str
    low: float
    high: float
    frozen_at: datetime | None
    freeze_at: datetime
    node_count: int
    evidence_count: int
    evidence_ids: tuple[str, ...]
    confidence: float
    algorithm_version: str


@dataclass(frozen=True)
class ReconcileOutcome:
    card_id: str | None
    blocked: str | None = None
    adjustments: tuple[str, ...] = ()
    moved_claims: tuple[str, ...] = ()


@dataclass(frozen=True)
class SettleResult:
    settled: tuple[tuple[str, str], ...]
    withdrawn_claims: tuple[str, ...] = ()
    sync: SyncResult | None = None


@dataclass(frozen=True)
class TemplateFull:
    key: str
    version: int
    target_type: str
    max_visibility: tuple[str, ...]
    max_ttl: timedelta
    text_template: str | None


class _Rollback(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


MasteryRows = Callable[[str], Awaitable[Sequence[MasteryRow]]]


def _dec(x: float) -> Decimal:
    return Decimal(f"{x:.1f}")


def _pg_error(e: asyncpg.PostgresError) -> StoreError:
    if isinstance(e, asyncpg.InsufficientPrivilegeError):
        return NotFound()
    if isinstance(e, asyncpg.ProgramLimitExceededError):
        return RateLimited()
    if isinstance(e, asyncpg.CheckViolationError):
        return Conflict()
    return Invalid()


class Sie03Store:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    # ---------- 公共 ----------
    @staticmethod
    async def _template(conn: asyncpg.Connection, key: str, version: int, target_type: str) -> TemplateFull:
        r = await conn.fetchrow("""SELECT template_key, version, target_type::text AS target_type, max_visibility, max_ttl, text_template
                                     FROM insight.claim_template WHERE template_key = $1 AND version = $2 AND status = 'approved'""",
                                key, version)
        if r is None or r["target_type"] != target_type:
            raise Unavailable("no approved template")
        return TemplateFull(r["template_key"], int(r["version"]), r["target_type"], tuple(r["max_visibility"]), r["max_ttl"],
                            r["text_template"])

    @staticmethod
    def _visibility(want: Sequence[str], tpl: TemplateFull) -> tuple[str, ...]:
        return tuple(a for a in AUDIENCE_ORDER if a in want and a in tpl.max_visibility)

    @staticmethod
    async def _claim(conn: asyncpg.Connection, student: str, claim_id: str, lock: bool = False) -> ClaimRow | None:
        r = await conn.fetchrow(f"SELECT {_CLAIM_COLS} FROM insight.claim c WHERE c.id = $1::uuid AND c.student_id = $2::uuid"
                                + (" FOR UPDATE" if lock else ""), claim_id, student)
        return None if r is None else claim_row(r)

    # ---------- 异议 ----------
    async def raise_dispute(self, student: str, claim_id: str, kind: str, actor_role: str, actor_ref: str,
                            scheduler: VerificationScheduler, params: Sie03Policy,
                            fact: tuple[str, str] | None = None) -> DisputeOutcome:
        """提出异议。actor_role / actor_ref 是请求里代表的人（学生本人或家长）；库里按关系再核，记下的身份必须与 actor_role 一致"""
        if kind not in KINDS or actor_role not in ("student", "guardian"):
            raise Invalid("kind or actor")
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await InsightStore._lock(conn, student)
                # 预检（安全审查 F3、契约审查 M1）：与 insight.raise_dispute 同一组判断条件——属于该学生、非终态、未过期、
                # 模板已审核、可见范围含请求受众；不满足一律 404，且在这之前不读任何 platform 表（身份由 ActorVerifier 在工具层先核）
                claim = await self._visible_claim(conn, student, claim_id, actor_role)
                if claim is None:
                    raise NotFound()
                if kind == "i_know" and (claim.target_type != "node" or claim.subject is None):
                    raise Invalid("verification is only available for node claims")
                fk, fid = (None, None)
                if kind == "data_error":
                    fk, fid = fact or await self._locate_fact(conn, student, claim)
                try:
                    async with conn.transaction():  # 保存点：库函数报错不污染外层事务的错误码判断
                        did = await conn.fetchval("SELECT insight.raise_dispute($1::uuid, $2, $3::uuid, NULL, $4, $5::uuid)",
                                                  claim_id, kind, actor_ref, fk, fid)
                except asyncpg.PostgresError as e:
                    raise _pg_error(e) from None
                d = await conn.fetchrow("""SELECT id, kind, status, raised_by, raised_by_role, verification_task_refs, created_at,
                                                  fact_kind, fact_id FROM insight.dispute WHERE id = $1""", did)
                # 提出人核对（lessons 待人确认 #10）：库里按关系认定的身份必须与请求的 actor 一致，否则整笔回滚、按“不存在”处理
                if d["raised_by_role"] != actor_role or str(d["raised_by"]) != actor_ref or d["kind"] != kind:
                    raise NotFound()
                ev = tuple(dict.fromkeys(claim.evidence_ids))
                if kind == "i_know":
                    out = await self._i_know(conn, student, claim, d, scheduler, params)
                    return DisputeOutcome(out.dispute_id, out.next_step, out.verification_item_refs, out.verify_by, evidence_ids=ev)
                if kind == "data_error":
                    return DisputeOutcome(str(did), NEXT_STEP[kind], fact_ref=fact_ref(d["fact_kind"], str(d["fact_id"])), evidence_ids=ev)
                return DisputeOutcome(str(did), NEXT_STEP[kind], evidence_ids=ev)

    @staticmethod
    async def _visible_claim(conn: asyncpg.Connection, student: str, claim_id: str, audience: str) -> ClaimRow | None:
        r = await conn.fetchrow(
            f"""SELECT {_CLAIM_COLS} FROM insight.claim c JOIN insight.claim_template t
                   ON t.template_key = c.template_key AND t.version = c.template_version
                 WHERE c.id = $1::uuid AND c.student_id = $2::uuid AND c.status IN ('hypothesis', 'established', 'to_verify')
                   AND c.expires_at > now() AND $3 = ANY (c.visibility) AND t.status = 'approved'
                 FOR UPDATE OF c""", claim_id, student, audience)
        return None if r is None else claim_row(r)

    @staticmethod
    async def _i_know(conn: asyncpg.Connection, student: str, claim: ClaimRow, d: Mapping[str, Any],
                      scheduler: VerificationScheduler, params: Sie03Policy) -> DisputeOutcome:
        assert claim.subject is not None
        if d["status"] == "open":
            if claim.status in ("hypothesis", "established"):
                n = await conn.execute("UPDATE insight.claim SET status = 'to_verify', change_reason = 'dispute' WHERE id = $1::uuid",
                                       claim.id)
                if n != "UPDATE 1":
                    raise RuntimeError("claim update did not hit exactly one row")
            tasks = await scheduler.schedule(student, claim.subject, claim.target_id, str(d["id"]), params.dispute.verification_items)
            if not 1 <= len(tasks) <= 5:
                raise RuntimeError("verification scheduler returned an unexpected number of tasks")
            await conn.execute("""UPDATE insight.dispute SET status = 'verifying', verification_task_refs = $2::uuid[]
                                   WHERE id = $1""", d["id"], [t.task_id for t in tasks])
            items = tuple(t.item_ref for t in tasks)
        else:  # 同一人对同一判断的未结束异议：返回原异议（幂等）
            items = tuple(await scheduler.describe(claim.subject, [str(x) for x in d["verification_task_refs"]]))
        return DisputeOutcome(str(d["id"]), NEXT_STEP["i_know"], items, verify_by(d["created_at"], params.dispute.verify_within_hours))

    @staticmethod
    async def _locate_fact(conn: asyncpg.Connection, student: str, claim: ClaimRow) -> tuple[str, str]:
        """契约的异议请求不带事实引用（lessons 契约差异）：由判断推出原始事实。
        - 考试类：判断指向考试记录（预测判断指向考试安排）；安排已有当前成绩时指成绩行；
        - 知识节点：判断证据里最近一条、能唯一对上作答（同一学生、学科、题目、发生时间）的证据 → 那次作答（待人确认的启发式）；
        对不上就报不可定位（不猜）"""
        if claim.target_type == "exam":
            r = await conn.fetchrow("""SELECT id, raw_score FROM insight.exam_record WHERE id::text = $1 AND student_id = $2::uuid""",
                                    claim.target_id, student)
            if r is not None:
                if r["raw_score"] is None:
                    s = await conn.fetchval("""SELECT id FROM insight.exam_record WHERE plan_record_id = $1 AND superseded_at IS NULL
                                                 ORDER BY created_at DESC, id LIMIT 1""", r["id"])
                    return "exam_record", str(s or r["id"])
                return "exam_record", str(r["id"])
        if claim.target_type == "node" and claim.evidence_ids:
            rows = await conn.fetch(
                """SELECT e.evidence_id, e.occurred_at, array_agg(a.attempt_id ORDER BY a.attempt_id) AS attempts
                     FROM platform.evidence_event e
                     JOIN platform.attempt a ON a.student_id = e.student_id AND a.subject = e.subject AND a.item_id = e.item_id
                                            AND a.occurred_at = e.occurred_at
                    WHERE e.student_id = $1::uuid AND e.evidence_id = ANY ($2::uuid[])
                    GROUP BY e.evidence_id, e.occurred_at ORDER BY e.occurred_at DESC, e.evidence_id""",
                student, list(claim.evidence_ids))
            for r in rows:
                if len(r["attempts"]) == 1:
                    return "attempt", str(r["attempts"][0])
        raise Unavailable("fact cannot be located")

    @staticmethod
    async def _fact_corrected(conn: asyncpg.Connection, kind: str, fact_id: Any) -> bool:
        """与 0032 守卫同一条件：考试记录已被替换、作答的判分已被改判"""
        if kind == "exam_record":
            return bool(await conn.fetchval("SELECT EXISTS (SELECT 1 FROM insight.exam_record WHERE id = $1 AND superseded_at IS NOT NULL)",
                                            fact_id))
        if kind == "attempt":
            return bool(await conn.fetchval("""SELECT EXISTS (SELECT 1 FROM platform.grade_result
                                                  WHERE attempt_id = $1 AND superseded_by IS NOT NULL)""", fact_id))
        return False

    async def settle_fact_corrections(self, student: str, rows: Sequence[MasteryRow] | None, policy: Policy) -> SettleResult:
        """“数据有误”：原始事实已在原录入通道更正的，相关判断撤回（fact_corrected）、异议以 fact_corrected 结案，
        再按判断生成重算（rows = KGE 更正后的当前掌握行；None 表示只撤回不重算）。一个事务"""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                now = await InsightStore._lock(conn, student)
                ds = await conn.fetch("""SELECT id, claim_id, fact_kind, fact_id FROM insight.dispute
                                          WHERE student_id = $1::uuid AND kind = 'data_error' AND status = 'verifying'
                                          ORDER BY created_at, id FOR UPDATE""", student)
                settled, withdrawn = [], []
                for d in ds:
                    if not await self._fact_corrected(conn, d["fact_kind"], d["fact_id"]):
                        continue
                    claim = await self._claim(conn, student, str(d["claim_id"]), lock=True)
                    assert claim is not None
                    if claim.status == "refuted":  # 已被推翻：没有可撤回的判断
                        await conn.execute("""UPDATE insight.dispute SET status = 'rejected', result_note_code = 'claim_no_longer_visible'
                                               WHERE id = $1""", d["id"])
                        settled.append((str(d["id"]), "claim_no_longer_visible"))
                        continue
                    for cid in await self._related(conn, student, claim):
                        await conn.execute("UPDATE insight.claim SET status = 'withdrawn', change_reason = 'fact_corrected' WHERE id = $1",
                                           cid)
                        withdrawn.append(str(cid))
                    await conn.execute("""UPDATE insight.dispute SET status = 'fact_corrected', result_note_code = 'fact_corrected'
                                           WHERE id = $1""", d["id"])
                    settled.append((str(d["id"]), "fact_corrected"))
                sync = None
                if settled and rows is not None:
                    sync = await InsightStore.sync_in(conn, student, rows, policy, now)
        return SettleResult(tuple(settled), tuple(withdrawn), sync)

    @staticmethod
    async def _related(conn: asyncpg.Connection, student: str, claim: ClaimRow) -> list[Any]:
        """相关判断：被异议的判断本身、同一依据行生成的判断（知识掌握与知识迁移）、同一对象同一模板的判断；只取未终结的"""
        rows = await conn.fetch(
            """SELECT id FROM insight.claim
                WHERE student_id = $1::uuid AND status IN ('hypothesis', 'established', 'to_verify')
                  AND (id = $2::uuid OR (basis_id IS NOT NULL AND basis_id = $3::uuid)
                       OR (target_type::text = $4 AND target_id = $5 AND template_key = $6))
                ORDER BY created_at, id FOR UPDATE""",
            student, claim.id, claim.basis_id, claim.target_type, claim.target_id, claim.template_key)
        return [r["id"] for r in rows]

    async def confirm_data(self, student: str, dispute_id: str) -> None:
        """复核结论“数据无误”：异议维持结案（判断不变）"""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await InsightStore._lock(conn, student)
                n = await conn.execute("""UPDATE insight.dispute SET status = 'resolved_unsupported', result_note_code = 'data_confirmed'
                                           WHERE id = $1::uuid AND student_id = $2::uuid AND kind = 'data_error' AND status = 'verifying'""",
                                       dispute_id, student)
                if n != "UPDATE 1":
                    raise NotFound()

    async def sweep_disputes(self, params: Sie03Policy, limit: int = 1000) -> dict[str, int]:
        """定时清理：
        - “那次只是失误”：判断已终结、过期或不再对学生可见 → rejected（claim_no_longer_visible）；观察期满 → resolved_unsupported
          （self_statement_recorded：只记下学生的说法，判断不变）；
        - “数据有误”：判断已终结且事实没有更正 → rejected；
        - “我其实会”：判断已终结而异议仍在验证中 → 计数 stuck（0032 没有对应的结案方式，待人确认）"""
        counts = {"slip_rejected": 0, "slip_recorded": 0, "data_error_rejected": 0, "i_know_stuck": 0}
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """SELECT d.id, d.kind, d.status, d.created_at, d.fact_kind, d.fact_id, c.status::text AS claim_status,
                              (c.expires_at <= now() OR NOT ('student' = ANY (c.visibility))) AS invisible, now() AS now
                         FROM insight.dispute d JOIN insight.claim c ON c.id = d.claim_id
                        WHERE d.status IN ('open', 'verifying') ORDER BY d.created_at, d.id LIMIT $1 FOR UPDATE OF d SKIP LOCKED""",
                    int(limit))
                for r in rows:
                    terminal = r["claim_status"] in TERMINAL
                    if r["kind"] == "one_off_slip":
                        if terminal or r["invisible"]:
                            await conn.execute("""UPDATE insight.dispute SET status = 'rejected', result_note_code = 'claim_no_longer_visible'
                                                   WHERE id = $1""", r["id"])
                            counts["slip_rejected"] += 1
                        elif r["created_at"] <= r["now"] - timedelta(days=params.dispute.one_off_slip_observe_days):
                            await conn.execute("""UPDATE insight.dispute SET status = 'resolved_unsupported',
                                                         result_note_code = 'self_statement_recorded' WHERE id = $1""", r["id"])
                            counts["slip_recorded"] += 1
                    elif r["kind"] == "data_error" and terminal and not await self._fact_corrected(conn, r["fact_kind"], r["fact_id"]):
                        await conn.execute("""UPDATE insight.dispute SET status = 'rejected', result_note_code = 'claim_no_longer_visible'
                                               WHERE id = $1""", r["id"])
                        counts["data_error_rejected"] += 1
                    elif r["kind"] == "i_know" and terminal:
                        counts["i_know_stuck"] += 1
        return counts

    async def dispute_metrics(self, since: datetime) -> dict[str, Any]:
        """指标“我其实会 24 小时内推送验证题 ≥ 95%”：进入验证（登记了验证任务）与提出在同一事务里，所以用“有验证任务引用的
        i_know 异议 / 全部 i_know 异议（不含提出人撤回）”计；分母为 0 时比率为空"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""SELECT id, verification_task_refs FROM insight.dispute
                                        WHERE kind = 'i_know' AND status <> 'withdrawn' AND created_at >= $1""", since)
        total = len(rows)
        stub = sum(1 for r in rows if r["verification_task_refs"]
                   and [str(x) for x in r["verification_task_refs"]] == stub_task_refs(str(r["id"]), len(r["verification_task_refs"])))
        real = sum(1 for r in rows if r["verification_task_refs"]) - stub
        # 接口桩期间的“推送”不是真实验证题：单独计数，比率标为无效（契约审查 7）
        valid = total > 0 and stub == 0
        return {"i_know_total": total, "verification_scheduled": real, "stub_scheduled": stub, "valid": valid,
                "rate": (real / total) if valid else None}

    # ---------- 预测 ----------
    async def predict(self, student: str, exam: Mapping[str, Any], rows: Sequence[MasteryRow], params: Sie03Policy,
                      weights: Mapping[str, float] | None = None, type_factors: Mapping[str, float] | None = None,
                      weights_source: str = "uniform") -> PredictOutcome:
        full = int(exam["full_score"])
        if full > params.exam.response_max_score:
            raise Invalid("full score exceeds the contract response range")
        types = EXAM_TYPES[exam["exam_type"]]
        exam_date = exam["exam_date"] if isinstance(exam["exam_date"], date) else date.fromisoformat(str(exam["exam_date"]))
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                now = await InsightStore._lock(conn, student)
                plans = await conn.fetch(
                    """SELECT id, full_score, scope_node_ids, exam_date FROM insight.exam_record
                        WHERE student_id = $1::uuid AND subject::text = $2 AND exam_type = ANY ($3::text[]) AND exam_date = $4::date
                          AND raw_score IS NULL AND superseded_at IS NULL ORDER BY id""",
                    student, exam["subject"], list(types), exam_date)
                if not plans:
                    raise NotFound()
                if len(plans) > 1:
                    raise Invalid("ambiguous exam plan")
                plan = plans[0]
                if float(plan["full_score"]) != float(full) or set(plan["scope_node_ids"]) != set(exam["scope_node_ids"]):
                    raise Invalid("exam does not match the recorded plan")
                freeze_at = await conn.fetchval("SELECT insight.exam_freeze_at($1::date)", plan["exam_date"])
                existing = await conn.fetchrow("SELECT * FROM insight.exam_prediction WHERE exam_record_id = $1 FOR UPDATE", plan["id"])
                if now >= freeze_at or (existing is not None and existing["frozen_at"] is not None):
                    if existing is None:
                        raise Conflict("prediction window closed")
                    return self._outcome(existing, freeze_at)
                current = await InsightStore.current_basis(conn, student, [r.basis_id for r in rows])
                rows = [r for r in rows if r.student_id == student and r.basis_id in current]  # 只用该学生的当前掌握行（契约审查 6）
                try:
                    pred = predict_interval(full, list(plan["scope_node_ids"]), rows, params.exam.interval, params.exam.scales,
                                            weights, type_factors)
                except PredictInputError:
                    raise Invalid("prediction input") from None
                if pred.evidence_count == 0:
                    raise Conflict("insufficient evidence")
                tpl = await self._template(conn, params.exam.prediction_template.key, params.exam.prediction_template.version, "exam")
                vis = self._visibility(params.exam.prediction_visibility, tpl)
                if not vis:
                    raise Unavailable("prediction visibility")  # 配置与模板上限没有交集：不回退到模板上限（安全审查 F1）
                expires = min(now + tpl.max_ttl, freeze_at + timedelta(days=params.exam.ttl_days_after_exam))
                value = {"low": pred.low, "high": pred.high, "full_score": full}
                ev = list(pred.evidence_ids)[-MAX_EVIDENCE:]
                claim_id = await self._upsert_exam_claim(conn, student, exam["subject"], str(plan["id"]), tpl, value, pred.confidence,
                                                         ev, MODEL_VERSION, vis, expires,
                                                         None if existing is None else str(existing["claim_id"]))
                basis = pred.basis(full, f"sie03-policy@{params.config_version}", weights_source)
                algo = MODEL_VERSION.removeprefix("algo:")
                if existing is None:
                    pid = await conn.fetchval(
                        """INSERT INTO insight.exam_prediction (student_id, exam_record_id, claim_id, interval_low, interval_high, basis,
                                                                evidence_ids, algorithm_version)
                           VALUES ($1::uuid, $2, $3::uuid, $4, $5, $6::jsonb, $7::uuid[], $8) RETURNING id""",
                        student, plan["id"], claim_id, _dec(pred.low), _dec(pred.high), basis, list(pred.evidence_ids), algo)
                else:
                    pid = existing["id"]
                    await conn.execute("""UPDATE insight.exam_prediction SET interval_low = $2, interval_high = $3, basis = $4::jsonb,
                                                 evidence_ids = $5::uuid[], algorithm_version = $6 WHERE id = $1""",
                                       pid, _dec(pred.low), _dec(pred.high), basis, list(pred.evidence_ids), algo)
                row = await conn.fetchrow("SELECT * FROM insight.exam_prediction WHERE id = $1", pid)
                return self._outcome(row, freeze_at)

    @staticmethod
    def _outcome(r: Mapping[str, Any], freeze_at: datetime) -> PredictOutcome:
        basis = r["basis"] or {}
        return PredictOutcome(str(r["id"]), str(r["claim_id"]), float(r["interval_low"]), float(r["interval_high"]), r["frozen_at"],
                              freeze_at, int(basis.get("node_count", 0)), int(basis.get("evidence_count", 0)),
                              tuple(str(e) for e in r["evidence_ids"]),
                              round(max(0.0, min(1.0, 1 - (float(r["interval_high"]) - float(r["interval_low"]))
                                                 / max(1.0, float(basis.get("full_score", 1))))), 3),
                              r["algorithm_version"])

    @staticmethod
    async def _upsert_exam_claim(conn: asyncpg.Connection, student: str, subject: str, target_id: str, tpl: TemplateFull,
                                 value: Mapping[str, Any], confidence: float, evidence: Sequence[str], model_version: str,
                                 visibility: Sequence[str], expires: datetime, existing_claim: str | None) -> str:
        """已有预测：只更新它挂的那条判断，那条已终结就 409，不另建（契约审查 M3）；没有预测：新建判断"""
        r = None
        if existing_claim is not None:
            r = await conn.fetchrow("""SELECT id, status::text AS status, value, confidence, evidence_ids FROM insight.claim
                                        WHERE id = $1::uuid AND student_id = $2::uuid FOR UPDATE""", existing_claim, student)
            if r is None or r["status"] in TERMINAL:
                raise Conflict("prediction claim is final")
        if r is None:
            return str(await conn.fetchval(
                """INSERT INTO insight.claim (student_id, subject, target_type, target_id, template_key, template_version, value,
                       confidence, evidence_ids, source_type, model_version, status, change_reason, visibility, expires_at)
                   VALUES ($1::uuid, $2::text::platform.subject, 'exam', $3, $4, $5, $6::jsonb, $7::text::numeric, $8::uuid[],
                           'inferred', $9, 'hypothesis', 'created', $10::text[], $11) RETURNING id""",
                student, subject, target_id, tpl.key, tpl.version, dict(value), str(confidence), list(evidence), model_version,
                list(visibility), expires))
        if dict(r["value"] or {}) != dict(value) or abs(float(r["confidence"]) - confidence) > 5e-4 \
                or [str(e) for e in r["evidence_ids"]] != list(evidence):
            await conn.execute("""UPDATE insight.claim SET value = $2::jsonb, confidence = $3::text::numeric, evidence_ids = $4::uuid[],
                                         change_reason = 'revised' WHERE id = $1""", r["id"], dict(value), str(confidence), list(evidence))
        return str(r["id"])

    # ---------- 对账 ----------
    async def reconcile_exam(self, student: str, score_record_id: str, params: Sie03Policy, forbidden: Forbidden,
                             node_results: Mapping[str, float] | None = None,
                             directions: Sequence[str] | None = None) -> ReconcileOutcome:
        """成绩上传后生成对账卡（一个事务；已有卡返回原卡）。不能生成时整笔回滚并返回阻断编码"""
        try:
            return await self._reconcile(student, score_record_id, params, forbidden, node_results, directions)
        except _Rollback as r:
            return ReconcileOutcome(None, blocked=r.code)

    async def _reconcile(self, student: str, score_record_id: str, params: Sie03Policy, forbidden: Forbidden,
                         node_results: Mapping[str, float] | None, directions: Sequence[str] | None) -> ReconcileOutcome:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                now = await InsightStore._lock(conn, student)
                e = await conn.fetchrow("""SELECT id, subject::text AS subject, raw_score, plan_record_id, superseded_at
                                             FROM insight.exam_record WHERE id = $1::uuid AND student_id = $2::uuid""", score_record_id, student)
                if e is None or e["raw_score"] is None:
                    raise NotFound()
                if e["superseded_at"] is not None or e["plan_record_id"] is None:
                    raise _Rollback("not_a_current_planned_score")
                p = await conn.fetchrow("SELECT * FROM insight.exam_prediction WHERE exam_record_id = $1", e["plan_record_id"])
                if p is None:
                    raise _Rollback("no_prediction")
                if p["frozen_at"] is None:
                    raise _Rollback("prediction_not_frozen")
                done = await conn.fetchrow("SELECT id, adjustments FROM insight.exam_reconciliation WHERE prediction_id = $1", p["id"])
                if done is not None:
                    return ReconcileOutcome(str(done["id"]), adjustments=tuple(done["adjustments"]))
                try:
                    plan = reconcile_plan(float(e["raw_score"]), float(p["interval_low"]), float(p["interval_high"]), p["basis"] or {},
                                          params.exam.interval.z, node_results, directions)
                except PredictInputError:
                    raise Invalid("reconcile input") from None
                if plan.blocked:
                    raise _Rollback(plan.blocked)
                tpl = await self._template(conn, params.exam.reconcile_template.key, params.exam.reconcile_template.version, "exam")
                vis = self._visibility(params.exam.reconcile_visibility, tpl)
                audience = [a for a in params.exam.card_audience if a in vis]
                if not audience:
                    raise _Rollback("no_audience")
                value = {"position": plan.position, "directions": list(plan.directions), "deviation_nodes": list(plan.deviation_nodes)}
                if len(json.dumps(value)) > 1800:
                    raise _Rollback("claim_value_too_large")  # 0010 value 上限 2048 字节
                if render_text(tpl.text_template, str(e["id"]), value, forbidden) is None:
                    raise _Rollback("render_blocked")  # 没有可渲染的结论或命中禁用推断词表：不出卡
                moved: list[str] = []
                if not plan.in_interval and plan.deviation_nodes:
                    for r in await conn.fetch("""SELECT id, status::text AS status FROM insight.claim
                                                  WHERE student_id = $1::uuid AND target_type = 'node' AND subject::text = $2
                                                    AND target_id = ANY ($3::text[]) AND status IN ('hypothesis', 'established')
                                                  ORDER BY target_id, id FOR UPDATE""", student, e["subject"], list(plan.deviation_nodes)):
                        await conn.execute("UPDATE insight.claim SET status = 'to_verify', change_reason = 'exam_deviation' WHERE id = $1",
                                           r["id"])
                        moved.append(str(r["id"]))
                adjustments = ("claims_to_verify",) if moved else ("none",)
                ev = [str(x) for x in p["evidence_ids"]][-MAX_EVIDENCE:]
                if not ev:
                    raise _Rollback("no_evidence")
                rc = str(await conn.fetchval(
                    """INSERT INTO insight.claim (student_id, subject, target_type, target_id, template_key, template_version, value,
                           confidence, evidence_ids, source_type, model_version, status, change_reason, visibility, expires_at)
                       VALUES ($1::uuid, $2::text::platform.subject, 'exam', $3, $4, $5, $6::jsonb, 1, $7::uuid[], 'inferred', $8,
                               'hypothesis', 'created', $9::text[], $10) RETURNING id""",
                    student, e["subject"], str(e["id"]), tpl.key, tpl.version, value, ev, RECONCILE_MODEL_VERSION, list(vis),
                    now + min(tpl.max_ttl, timedelta(days=params.exam.ttl_days_after_exam))))
                stored = await conn.fetchrow("SELECT value FROM insight.claim WHERE id = $1::uuid", rc)
                directions = tuple(stored["value"].get("directions") or ())      # 卡片的方向只从判断账本取（契约审查 M2）
                dev_nodes = tuple(stored["value"].get("deviation_nodes") or ())
                sentences = [{"claim_id": rc, "template_key": tpl.key}]
                sentences += [{"claim_id": rc, "template_key": tpl.key, "direction": d} for d in directions]
                sc = {rc: SentenceClaim(rc, student, tpl.key, True, "hypothesis", vis,
                                        now + min(tpl.max_ttl, timedelta(days=params.exam.ttl_days_after_exam)), directions)}
                if check_sentences(sentences, sc, student, audience, now):
                    raise _Rollback("sentence_check_failed")
                card = await conn.fetchval(
                    """INSERT INTO insight.exam_reconciliation (student_id, prediction_id, exam_record_id, in_interval, deviation_directions,
                           deviation_nodes, adjustments, audience, sentences, evidence_ids, algorithm_version)
                       VALUES ($1::uuid, $2, $3, $4, $5::text[], $6::text[], $7::text[], $8::text[], $9::jsonb, $10::uuid[], $11)
                       RETURNING id""",
                    student, p["id"], e["id"], plan.in_interval, list(directions), list(dev_nodes), list(adjustments),
                    audience, sentences, ev, RECONCILE_MODEL_VERSION.removeprefix("algo:"))
                return ReconcileOutcome(str(card), adjustments=adjustments, moved_claims=tuple(moved))

    async def read_reconciliation(self, student: str, exam_record_id: str) -> Mapping[str, Any] | None:
        """对账卡（只读）：按成绩记录或考试安排的 ID 找，必须属于该学生"""
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                """SELECT r.id, r.prediction_id, r.in_interval, r.deviation_directions, r.deviation_nodes, r.adjustments, r.sentences,
                          r.evidence_ids, r.algorithm_version, e.raw_score
                     FROM insight.exam_reconciliation r
                     JOIN insight.exam_record e ON e.id = r.exam_record_id
                     JOIN insight.exam_prediction p ON p.id = r.prediction_id
                    WHERE r.student_id = $1::uuid AND (r.exam_record_id = $2::uuid OR p.exam_record_id = $2::uuid)
                    ORDER BY r.created_at DESC LIMIT 1""", student, exam_record_id)

    # ---------- 触发规则 ----------
    async def evaluate_triggers(self, student: str, params: Sie03Policy) -> list[tuple[str, Suggestion]]:
        """评估触发规则 v1，只写建议（suggested）。返回（记录 ID, 建议）"""
        t = params.triggers
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                now = await InsightStore._lock(conn, student)
                claims = [claim_row(r) for r in await conn.fetch(
                    f"""SELECT {_CLAIM_COLS} FROM insight.claim c WHERE c.student_id = $1::uuid
                         AND c.status IN ('hypothesis', 'established') AND c.expires_at > now() ORDER BY c.created_at, c.id""", student)]
                since = now - timedelta(days=t.encourage_lookback_days)
                transitions = tuple(Transition(str(r["claim_id"]), r["to_status"], r["reason"], r["at"]) for r in await conn.fetch(
                    """SELECT h.claim_id, h.to_status::text AS to_status, h.reason, h.at FROM insight.claim_history h
                         JOIN insight.claim c ON c.id = h.claim_id
                        WHERE c.student_id = $1::uuid AND h.at > $2 ORDER BY h.at, h.id""", student, since))
                far = now - timedelta(days=LOOKBACK_DAYS)
                last = await conn.fetchrow(
                    """SELECT (SELECT max(occurred_at) FROM insight.behavior_event
                                WHERE student_id = $1::uuid AND kind = 'session_start' AND occurred_at > $2 AND occurred_at <= $3) AS s,
                              (SELECT max(occurred_at) FROM platform.attempt
                                WHERE student_id = $1::uuid AND occurred_at > $2 AND occurred_at <= $3) AS a""", student, far, now)
                activity = tuple(x for x in (last["s"], last["a"]) if x is not None)
                w = timedelta(days=t.exit_after_errors_window_days)
                exits = tuple(r["occurred_at"] for r in await conn.fetch(
                    """SELECT occurred_at FROM insight.behavior_event WHERE student_id = $1::uuid AND kind = 'exit_after_errors'
                         AND occurred_at > $2 AND occurred_at <= $3 ORDER BY occurred_at""", student, now - 2 * w, now))
                logged = tuple(LoggedAction(r["rule_key"], r["action"], frozenset(str(x) for x in r["claim_ids"]), r["created_at"])
                               for r in await conn.fetch("""SELECT rule_key, action, claim_ids, created_at FROM insight.intervention_log
                                                             WHERE student_id = $1::uuid AND created_at > $2""",
                                                         student, now - timedelta(days=t.dedup_days)))
                suggestions = evaluate(TriggerFacts(tuple(claims), transitions, activity, exits, logged), t, now)
                check_suggestions(suggestions, claims, student, now)
                out = []
                for s in suggestions:
                    lid = await conn.fetchval("""INSERT INTO insight.intervention_log (student_id, rule_key, action, claim_ids, params_version)
                                                 VALUES ($1::uuid, $2, $3, $4::uuid[], $5) RETURNING id""",
                                              student, s.rule_key, s.action, list(s.claim_ids), t.params_version)
                    out.append((str(lid), s))
                return out

    async def dispatch_triggers(self, student: str, params: Sie03Policy, result_refs: Mapping[str, str] | None = None) -> int:
        """下发建议（suggested → dispatched）：配置未签字一律不下发（Unavailable）；库里再核签字登记（同规则、同参数版本）"""
        if not params.signed:
            raise Unavailable("trigger parameters are not signed")
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await InsightStore._lock(conn, student)
                rows = await conn.fetch("""SELECT id FROM insight.intervention_log WHERE student_id = $1::uuid AND status = 'suggested'
                                             AND params_version = $2 ORDER BY created_at, id FOR UPDATE""", student, params.triggers.params_version)
                try:
                    for r in rows:
                        await conn.execute("UPDATE insight.intervention_log SET status = 'dispatched', result_ref = $2::uuid WHERE id = $1",
                                           r["id"], (result_refs or {}).get(str(r["id"])))
                except asyncpg.InsufficientPrivilegeError:
                    raise Unavailable("rule not signed off in the database") from None
                except asyncpg.CheckViolationError:
                    raise Unavailable("a referenced claim is no longer usable") from None  # 下发时库里再核判断（0033）
                return len(rows)

