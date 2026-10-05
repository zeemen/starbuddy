"""洞察服务的数据库层：只以 starbuddy_insight 连接（迁移 0010、0022、0025、0028 已授的权限）。

两道防线：
1. 数据库：登录角色只继承 starbuddy_insight，连接建立后 SET ROLE starbuddy_insight；0010 的触发器校验每一次写入
   （状态机、原因白名单、模板已审核、可见范围上限、有效期上限、依据属于同一学生），历史由属主为 authz 的触发器追加。
   启动时断言：不是超级用户、没有 BYPASSRLS / CREATEROLE / CREATEDB，不继承其他应用角色（bff、learning、review、scheduler、
   kge、kge_read、report、authz），不是任何 pg_ 预置角色的成员（assert_insight_role），不满足即拒绝启动。
2. 本模块的读取 SQL 自己按可见范围、状态、到期、模板已审核过滤（与 0010 的前端 / 学科策略同一条件；洞察服务自己的 RLS 是全放行，
   所以这一道是必需的），中圈指标（metric）对学生、家长一律不返回。

写入：同一学生的判断生成在一个事务里，先取事务级 advisory lock，再读现有判断与历史、按 claims.plan_* 生成操作、逐条执行
（每条修改都在同一条 UPDATE 里写 change_reason）。时间一律取数据库的 now()（触发器用它校验有效期），不用进程时钟。
读 platform 只用 0028 授予的列；不读证据原文（payload、media_ref 没有授权），不写任何 platform 表。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

import asyncpg

from .claims import ClaimRow, MasteryRow, Op, TemplateInfo, _expiry, _fields_from, plan_node_claims, unit_of
from .dispute import i_know_outcome
from .metrics import (ActivityFact, AttemptFact, ErrorFact, MetricInputs, MetricResult, PlanFact, ProbeFact, compute_metrics,
                      plan_metric_claims)
from .policy import NODE_CATEGORIES, Policy
from .report import MAX_REPORT_CLAIMS, ReportClaim
from .render import ClaimForRender
from .shared import Forbidden

ROLE = "starbuddy_insight"
OTHER_APP_ROLES = ("starbuddy_bff", "starbuddy_learning", "starbuddy_review", "starbuddy_scheduler", "starbuddy_kge",
                   "starbuddy_kge_read", "starbuddy_report", "starbuddy_authz", "starbuddy_eraser")
SERVER_SETTINGS = {"application_name": "starbuddy-insight", "TimeZone": "Asia/Shanghai"}
LIVE = ("hypothesis", "established", "to_verify")
FRONT = ("student", "guardian")
LOOKBACK_DAYS = 180  # 中圈指标读取的最长回看（迁移差、保持率、自评校准用全部可得数据，限在这个范围内）


class RoleError(RuntimeError):
    """数据库连接的角色不安全：拒绝启动"""


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.execute(f"SET ROLE {ROLE}")
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def assert_insight_role(conn: asyncpg.Connection) -> None:
    row = await conn.fetchrow(
        """SELECT current_user::text AS cur, r.rolsuper, r.rolbypassrls, r.rolcreaterole, r.rolcreatedb,
                  pg_has_role(session_user, $1, 'MEMBER') AS is_insight,
                  ARRAY(SELECT x FROM unnest($2::text[]) x WHERE pg_has_role(session_user, x, 'MEMBER')) AS others,
                  ARRAY(SELECT p.rolname::text FROM pg_roles p
                         WHERE p.rolname LIKE 'pg\\_%' AND pg_has_role(session_user, p.oid, 'MEMBER')
                         ORDER BY p.rolname) AS predefined
             FROM pg_roles r WHERE r.rolname = session_user""", ROLE, list(OTHER_APP_ROLES))
    if row is None:
        raise RoleError("无法确认登录角色")
    if row["rolsuper"]:
        raise RoleError("洞察服务不能以超级用户连接数据库")
    if row["rolbypassrls"]:
        raise RoleError("洞察服务的登录角色不能有 BYPASSRLS")
    if row["rolcreaterole"] or row["rolcreatedb"]:
        raise RoleError("洞察服务的登录角色不能有 CREATEROLE 或 CREATEDB")
    if row["predefined"]:
        raise RoleError("洞察服务的登录角色不能是 pg_ 预置角色的成员（pg_read_all_data、pg_write_all_data 等）")
    if not row["is_insight"]:
        raise RoleError(f"登录角色必须继承 {ROLE}")
    if row["others"]:
        raise RoleError(f"登录角色不能同时继承 {ROLE} 以外的应用角色")
    if row["cur"] != ROLE:
        raise RoleError(f"当前角色必须是 {ROLE}")
    cur = await conn.fetchrow("SELECT r.rolsuper, r.rolbypassrls, r.rolcreaterole, r.rolcreatedb FROM pg_roles r WHERE r.rolname = current_user")
    if cur is None or cur["rolsuper"] or cur["rolbypassrls"] or cur["rolcreaterole"] or cur["rolcreatedb"]:
        raise RoleError(f"{ROLE} 的属性不安全")


async def create_pool(dsn: str | None = None, **connect: Any) -> asyncpg.Pool:
    """建池并断言角色安全；不满足即关闭池并报错（fail closed）"""
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=10, init=_init_connection, server_settings=SERVER_SETTINGS, **connect)
    try:
        async with pool.acquire() as conn:
            await assert_insight_role(conn)
    except BaseException:
        await pool.close()
        raise
    return pool


def _s(v: Any) -> str | None:
    return None if v is None else str(v)


def _f(v: Any) -> float:
    return float(v)


_CLAIM_COLS = ("c.id, c.student_id, c.subject::text AS subject, c.target_type::text AS target_type, c.target_id, c.template_key, "
               "c.template_version, c.value, c.confidence, c.evidence_ids, c.basis_kind::text AS basis_kind, c.basis_id, "
               "c.source_type::text AS source_type, c.model_version::text AS model_version, c.status::text AS status, c.visibility, "
               "c.created_at, c.expires_at, c.updated_at")


def claim_row(r: Mapping[str, Any]) -> ClaimRow:
    return ClaimRow(id=str(r["id"]), student_id=str(r["student_id"]), subject=r["subject"], target_type=r["target_type"],
                    target_id=r["target_id"], template_key=r["template_key"], template_version=int(r["template_version"]),
                    value=dict(r["value"] or {}), confidence=_f(r["confidence"]), evidence_ids=tuple(str(e) for e in r["evidence_ids"]),
                    basis_kind=r["basis_kind"], basis_id=_s(r["basis_id"]), source_type=r["source_type"],
                    model_version=r["model_version"], status=r["status"], visibility=tuple(r["visibility"]),
                    created_at=r["created_at"], expires_at=r["expires_at"], updated_at=r["updated_at"],
                    to_verify_since=r.get("to_verify_since"))


@dataclass(frozen=True)
class VisibleClaim:
    row: ClaimRow
    text_template: str | None

    def for_render(self) -> ClaimForRender:
        c = self.row
        return ClaimForRender(c.id, c.template_key, c.template_version, self.text_template, c.status, c.target_id, c.value,
                              c.confidence)


@dataclass(frozen=True)
class SyncResult:
    ops: tuple[Op, ...]
    skipped_rows: int = 0
    missing_templates: tuple[str, ...] = ()
    settled: tuple[tuple[str, str], ...] = ()  # （异议 ID, 结论）：同一事务里按新掌握行结案的“我其实会”异议（SIE-03）


def _visible_where(audience: str) -> str:
    """与 0010 claim_front_read / claim_agent_read 同一条件（洞察服务自己的 RLS 全放行，这里是必需的一道）"""
    cond = ("c.status IN ('hypothesis', 'established', 'to_verify') AND c.expires_at > now() AND $2 = ANY (c.visibility) "
            "AND t.status = 'approved'")
    if audience in FRONT:
        cond += " AND c.target_type <> 'metric'"  # 中圈指标对学生、家长一律不返回（spec 设计要点 5）
    return cond


class InsightStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    # ---------- 读：可见判断 ----------
    async def visible_claims(self, student: str, audience: str, *, subject: str | None = None, target_type: str | None = None,
                             statuses: Sequence[str] | None = None, limit: int = 200,
                             claim_id: str | None = None) -> list[VisibleClaim]:
        if audience not in ("agent", *FRONT):
            raise ValueError("unknown audience")
        sql = (f"SELECT {_CLAIM_COLS}, t.text_template FROM insight.claim c JOIN insight.claim_template t "
               "ON t.template_key = c.template_key AND t.version = c.template_version "
               f"WHERE c.student_id = $1::uuid AND {_visible_where(audience)} "
               "AND ($3::text IS NULL OR c.subject::text = $3) AND ($4::text IS NULL OR c.target_type::text = $4) "
               "AND ($5::text[] IS NULL OR c.status::text = ANY ($5)) AND ($6::uuid IS NULL OR c.id = $6) "
               "ORDER BY c.target_type, c.subject NULLS FIRST, c.target_id, c.template_key, c.created_at, c.id LIMIT $7")
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(sql, student, audience, subject, target_type, list(statuses) if statuses else None,
                                    claim_id, int(limit))
        return [VisibleClaim(claim_row(r), r["text_template"]) for r in rows]

    async def evidence(self, student: str, ids: Sequence[str]) -> list[Mapping[str, Any]]:
        """解释用的证据链：只取 0028 授予的列（不读 payload、media_ref）"""
        if not ids:
            return []
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """SELECT evidence_id, occurred_at, evidence_level, source_type FROM platform.evidence_event
                    WHERE student_id = $1::uuid AND evidence_id = ANY ($2::uuid[]) ORDER BY occurred_at, evidence_id LIMIT 200""",
                student, list(ids))

    async def report_claims(self, student: str, audience: str) -> list[ReportClaim]:
        if audience not in FRONT:
            raise ValueError("report audience must be student or guardian")
        out = []
        # 报告单独上限（不沿用 visible_claims 缺省 200 条静默截断）：多取一条，超过 MAX_REPORT_CLAIMS 由 build_first_report 整份阻断
        for v in await self.visible_claims(student, audience, target_type="node", statuses=("hypothesis", "established"),
                                           limit=MAX_REPORT_CLAIMS + 1):
            out.append(ReportClaim(v.for_render(), v.row.subject, v.row.target_type, v.row.visibility, v.row.expires_at,
                                   evidence_ids=tuple(str(e) for e in v.row.evidence_ids)))
        return out

    # ---------- 写：判断生成 ----------
    @staticmethod
    async def _lock(conn: asyncpg.Connection, student: str) -> datetime:
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('insight.claims:' || $1::text, 0))", student)
        return await conn.fetchval("SELECT now()")

    @staticmethod
    async def _templates(conn: asyncpg.Connection, policy: Policy, categories: Sequence[str]) -> tuple[dict[str, TemplateInfo], list[str]]:
        found: dict[str, TemplateInfo] = {}
        missing: list[str] = []
        for cat in categories:
            ref = policy.templates[cat]
            r = await conn.fetchrow("""SELECT template_key, version, target_type::text AS target_type, max_visibility, max_ttl
                                         FROM insight.claim_template WHERE template_key = $1 AND version = $2 AND status = 'approved'""",
                                    ref.key, ref.version)
            want_type = "metric" if cat == "metric" else "node"
            if r is None or r["target_type"] != want_type:
                missing.append(cat)
                continue
            found[cat] = TemplateInfo(r["template_key"], int(r["version"]), tuple(r["max_visibility"]), r["max_ttl"])
        return found, missing

    @staticmethod
    async def _live(conn: asyncpg.Connection, student: str, keys: Sequence[str]) -> list[ClaimRow]:
        rows = await conn.fetch(
            f"""SELECT {_CLAIM_COLS},
                       (SELECT max(h.at) FROM insight.claim_history h WHERE h.claim_id = c.id AND h.to_status = 'to_verify'
                           AND h.from_status IS DISTINCT FROM 'to_verify') AS to_verify_since
                  FROM insight.claim c
                 WHERE c.student_id = $1::uuid AND c.status IN ('hypothesis', 'established', 'to_verify') AND c.template_key = ANY ($2::text[])
                 ORDER BY c.created_at, c.id FOR UPDATE OF c""", student, list(keys))
        return [claim_row(r) for r in rows]

    @staticmethod
    async def _apply(conn: asyncpg.Connection, ops: Sequence[Op]) -> list[str]:
        created = []
        for op in ops:
            f = dict(op.fields)
            if op.claim_id is None:
                cid = await conn.fetchval(
                    """INSERT INTO insight.claim (student_id, subject, target_type, target_id, template_key, template_version, value,
                           confidence, evidence_ids, basis_kind, basis_id, source_type, model_version, status, change_reason,
                           visibility, expires_at)
                       VALUES ($1::uuid, $2::text::platform.subject, $3::text::insight.claim_target, $4, $5, $6, $7::jsonb,
                               $8::text::numeric, $9::uuid[], $10::text::insight.basis_kind, $11::uuid, $12::text::insight.source_type,
                               $13, $14::text::insight.claim_status, $15, $16::text[], $17)
                       RETURNING id""",
                    f["student_id"], f["subject"], f["target_type"], f["target_id"], f["template_key"], f["template_version"],
                    dict(f["value"]), str(f["confidence"]), list(f["evidence_ids"]), f["basis_kind"], f["basis_id"],
                    f["source_type"], f["model_version"], op.to_status, op.reason, list(f["visibility"]), f["expires_at"])
                created.append(str(cid))
                continue
            sets = ["status = $2::text::insight.claim_status", "change_reason = $3"]
            args: list[Any] = [op.claim_id, op.to_status, op.reason]
            casts = {"value": "jsonb", "confidence": "text::numeric", "evidence_ids": "uuid[]", "basis_kind": "text::insight.basis_kind",
                     "basis_id": "uuid", "model_version": "text", "visibility": "text[]", "expires_at": "timestamptz"}
            for k in sorted(f):
                v = f[k]
                if k in ("evidence_ids", "visibility"):
                    v = list(v)
                elif k == "confidence":
                    v = str(v)
                elif k == "value":
                    v = dict(v)
                args.append(v)
                sets.append(f"{k} = ${len(args)}::{casts[k]}")
            n = await conn.execute(f"UPDATE insight.claim SET {', '.join(sets)} WHERE id = $1::uuid", *args)
            if n != "UPDATE 1":
                raise RuntimeError("claim update did not hit exactly one row")
        return created

    @staticmethod
    async def current_basis(conn: asyncpg.Connection, student: str, ids: Sequence[str]) -> set[str]:
        rows = await conn.fetch("""SELECT id FROM platform.mastery_estimate
                                    WHERE student_id = $1::uuid AND id = ANY ($2::uuid[]) AND is_current""", student, list(ids))
        return {str(r["id"]) for r in rows}

    async def sync_mastery_claims(self, student: str, rows: Sequence[MasteryRow], policy: Policy) -> SyncResult:
        """按当前掌握行生成与流转节点判断（一个事务）。rows 应是该学生的全部当前掌握行：不在其中的单元视为依据已撤下。
        掌握行的 basis_id 必须是库里该学生的当前 mastery_estimate 行，否则按“没有依据”处理（不写、并会撤回引用它的判断）"""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                now = await self._lock(conn, student)
                return await self.sync_in(conn, student, rows, policy, now)

    @classmethod
    async def sync_in(cls, conn: asyncpg.Connection, student: str, rows: Sequence[MasteryRow], policy: Policy,
                      now: datetime) -> SyncResult:
        """sync_mastery_claims 的事务内部分（调用方已取学生锁）：SIE-03 的事实更正重算也走这里（同一套判断生成）。
        先按新掌握行结案“我其实会”异议（验证结果就是 KGE 的掌握行，dispute.i_know_outcome），再读现有判断、生成操作、逐条写"""
        if any(r.student_id != student for r in rows):
            raise ValueError("rows must belong to the student")
        for r in rows:
            r.check()
        templates, missing = await cls._templates(conn, policy, ("node_mastery", "node_transfer"))
        current = await cls.current_basis(conn, student, [r.basis_id for r in rows])
        usable = [r for r in rows if r.basis_id in current]
        settled = await cls._settle_i_know(conn, student, usable, policy, now, templates)
        keys = [policy.templates[c].key for c in ("node_mastery", "node_transfer")]
        existing = await cls._live(conn, student, keys)
        ops = plan_node_claims(existing, usable, policy, now, templates)
        await cls._apply(conn, ops)
        return SyncResult(tuple(ops), len(rows) - len(usable), tuple(missing), tuple(settled))

    @classmethod
    async def _settle_i_know(cls, conn: asyncpg.Connection, student: str, rows: Sequence[MasteryRow], policy: Policy,
                             now: datetime, templates: Mapping[str, TemplateInfo]) -> list[tuple[str, str]]:
        """验证中的“我其实会”异议：按同一单元（学科、节点、认知层次）的新掌握行结案（0032 的流转表 + 0010 的状态机，同一事务）。
        - supported：原判断推翻（verification_refuted），异议 resolved_supported；随后的判断生成按新行重算出新判断（上调）；
        - not_supported：原判断回到成立（verification_supported，数值与证据按新行，需策略已签字），异议 resolved_unsupported；
        - conflict / pending / pending_signoff：不写（冲突的处理待人确认，见 lessons）"""
        ds = await conn.fetch("""SELECT id, claim_id, created_at FROM insight.dispute
                                  WHERE student_id = $1::uuid AND kind = 'i_know' AND status = 'verifying'
                                  ORDER BY created_at, id FOR UPDATE""", student)
        by_unit = {(r.subject, r.node_id, r.cognitive_level): r for r in rows}
        out: list[tuple[str, str]] = []
        for d in ds:
            c = await conn.fetchrow(f"SELECT {_CLAIM_COLS} FROM insight.claim c WHERE c.id = $1 FOR UPDATE", d["claim_id"])
            claim = claim_row(c)
            unit = unit_of(claim, policy)
            if claim.status != "to_verify" or unit is None:
                out.append((str(d["id"]), "stuck" if claim.status in ("refuted", "withdrawn") else "pending"))
                continue
            # 判断所述与证据取异议时的历史快照（其间的常规修改可能已把验证证据并进判断，不能拿当前行比）
            snap = await conn.fetchrow("""SELECT value, evidence_ids FROM insight.claim_history WHERE claim_id = $1 AND reason = 'dispute'
                                           ORDER BY at DESC LIMIT 1""", claim.id)
            said = dict(snap["value"]) if snap else dict(claim.value)
            said_ev = tuple(str(e) for e in snap["evidence_ids"]) if snap else claim.evidence_ids
            level, gate = said.get("level"), said.get("gate")
            row = by_unit.get((claim.subject, claim.target_id, level))
            outcome = i_know_outcome(gate if isinstance(gate, str) else None, said_ev, d["created_at"], row)
            if outcome == "supported":
                await cls._apply(conn, [Op(claim.id, unit, "to_verify", "refuted", "verification_refuted")])
                await conn.execute("""UPDATE insight.dispute SET status = 'resolved_supported', result_note_code = 'verification_supported'
                                       WHERE id = $1""", d["id"])
            elif outcome == "not_supported":
                cat = unit[0]
                tpl = templates.get(cat)
                if not policy.signed or tpl is None or cat not in NODE_CATEGORIES:
                    outcome = "pending_signoff"
                else:
                    assert row is not None
                    fields = _fields_from(row)
                    fields["expires_at"] = _expiry(now, policy.ttl_days[cat], tpl, row.next_retest_at)
                    await cls._apply(conn, [Op(claim.id, unit, "to_verify", "established", "verification_supported", fields)])
                    await conn.execute("""UPDATE insight.dispute SET status = 'resolved_unsupported',
                                                 result_note_code = 'verification_not_supported' WHERE id = $1""", d["id"])
            out.append((str(d["id"]), outcome))
        return out

    async def recompute_metrics(self, student: str, policy: Policy, forbidden: Forbidden,
                                tags: Mapping[tuple[str, str], tuple[str, str]] | None = None) -> tuple[list[MetricResult], SyncResult]:
        """中圈指标：读事实 → 计算 → 写 visibility = {agent} 的 metric 判断（一个事务）。
        tags：(学科, 题目 ID) → (节点 ID, 认知层次)；洞察服务暂无 kge.item_tag 读权限（lessons 待人确认），由调用方注入"""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                now = await self._lock(conn, student)
                inputs = await load_metric_inputs(conn, student, now, tags)
                results = compute_metrics(inputs, policy, now, forbidden)
                templates, missing = await self._templates(conn, policy, ("metric",))
                existing = await self._live(conn, student, [policy.templates["metric"].key])
                ops = plan_metric_claims(existing, results, policy, now, templates.get("metric"), student)
                await self._apply(conn, ops)
        return results, SyncResult(tuple(ops), 0, tuple(missing))

    async def sweep_expired(self, limit: int = 1000) -> int:
        """每天的到期扫描：到期的假设 / 成立判断转待验证（原因 expired）。返回转了多少条"""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch("""SELECT id, status::text AS status FROM insight.claim
                                            WHERE status IN ('hypothesis', 'established') AND expires_at <= now()
                                            ORDER BY expires_at, id LIMIT $1 FOR UPDATE SKIP LOCKED""", int(limit))
                for r in rows:
                    await conn.execute("UPDATE insight.claim SET status = 'to_verify', change_reason = 'expired' WHERE id = $1", r["id"])
        return len(rows)


async def load_metric_inputs(conn: asyncpg.Connection, student: str, now: datetime,
                             tags: Mapping[tuple[str, str], tuple[str, str]] | None = None) -> MetricInputs:
    """只读 0028 授予的列：作答（提示等级、作答情境）、当前判分、错因大类；以及 0025 的探针与行为事件（计划、会话）"""
    since = now - timedelta(days=LOOKBACK_DAYS)
    tags = tags or {}
    att = await conn.fetch(
        """SELECT a.attempt_id, a.subject::text AS subject, a.item_id::text AS item_id, a.occurred_at, a.hint_level_used,
                  a.evidence_kind, g.verdict
             FROM platform.attempt a
             LEFT JOIN LATERAL (SELECT verdict FROM platform.grade_result g
                                 WHERE g.attempt_id = a.attempt_id AND g.student_id = a.student_id AND g.superseded_by IS NULL
                                 ORDER BY g.created_at DESC, g.id LIMIT 1) g ON true
            WHERE a.student_id = $1::uuid AND a.occurred_at > $2 AND a.occurred_at <= $3
            ORDER BY a.occurred_at, a.attempt_id""", student, since, now)
    attempts = []
    for r in att:
        node, level = tags.get((r["subject"], r["item_id"]), (None, None))
        attempts.append(AttemptFact(str(r["attempt_id"]), r["subject"], r["item_id"], r["occurred_at"],
                                    None if r["hint_level_used"] is None else int(r["hint_level_used"]), r["evidence_kind"],
                                    r["verdict"], node, level))
    prb = await conn.fetch(
        """SELECT p.id, p.subject::text AS subject, p.asked_at, p.prediction, g.verdict
             FROM insight.confidence_probe p
             LEFT JOIN LATERAL (SELECT verdict FROM platform.grade_result g
                                 WHERE g.attempt_id = p.attempt_id AND g.student_id = p.student_id AND g.superseded_by IS NULL
                                 ORDER BY g.created_at DESC, g.id LIMIT 1) g ON p.attempt_id IS NOT NULL
            WHERE p.student_id = $1::uuid AND p.asked_at > $2 AND p.asked_at <= $3 ORDER BY p.asked_at, p.id""", student, since, now)
    probes = [ProbeFact(str(r["id"]), r["subject"], r["asked_at"], r["prediction"], r["verdict"]) for r in prb]
    beh = await conn.fetch(
        """SELECT id, kind, payload ->> 'plan_task_id' AS task, occurred_at FROM insight.behavior_event
            WHERE student_id = $1::uuid AND kind IN ('plan_done', 'plan_skip', 'session_start') AND occurred_at > $2 AND occurred_at <= $3
            ORDER BY occurred_at, id""", student, since, now)
    plans = [PlanFact(str(r["id"]), r["kind"], r["task"], r["occurred_at"]) for r in beh if r["kind"] != "session_start" and r["task"]]
    activity = [ActivityFact(str(r["id"]), r["occurred_at"]) for r in beh if r["kind"] == "session_start"]
    activity += [ActivityFact(a.fact_id, a.occurred_at) for a in attempts]
    err = await conn.fetch(
        """SELECT id, subject::text AS subject, cross_subject_category::text AS category FROM platform.error_attribution
            WHERE student_id = $1::uuid AND superseded_by IS NULL AND created_at > $2 ORDER BY id""", student, since)
    errors = [ErrorFact(str(r["id"]), r["subject"], r["category"]) for r in err]
    return MetricInputs(tuple(attempts), tuple(probes), tuple(plans), tuple(activity), tuple(errors))
