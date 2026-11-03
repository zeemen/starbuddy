"""月度计划的数据库层（迁移 0054 草稿；PLT-14 第 2 步）：只以 starbuddy_insight 连接（与 store.InsightStore 同一个连接池、同一套角色断言）。

- 顺序（与 0054 insight.save_monthly_plan 一致）：月份须是本月 → 已有本月快照先返回（不写新行）→ 再查权益 → 读输入、生成、落库；
- 权益、诉求与复测：经 insight.monthly_plan_facts（属主 authz）读——洞察服务对诉求表、复测任务表、订阅与权益表都没有表权限；
  诉求只拿当前有效绑定家长的当前版里的关注学科与目标分（不读补充说明与学校）；权益 = 当前主订阅的 weekly_report 取值含 monthly；
- 判断：沿用 InsightStore.visible_claims（与 0010 前端读取同一组条件），知识节点判断按学生、家长两个受众各读一次合并，
  考试判断按家长受众读；每条另读 claim_history 的最新版本（落库进 input_refs）与门槛首次进入“迁移”的时刻（上月点亮用）；
- 掌握：MasterySource.all_rows（缺省 DbMasterySource：platform.mastery_estimate 的节点键当前行，0028 已授）；
- 成绩：insight.exam_record（0022 已授洞察服务只读）每科最近一次当前成绩，与本月的考试安排；
- 写：insight.save_monthly_plan（属主 authz，只授洞察服务）；库里守卫再逐项核对引用。时间一律取数据库的 now()。不计量（自动月报不计次）。
- 重放：replay_inputs 按快照里的 input_refs 找回同一组输入（判断按 claim_history 版本还原、诉求版本与复测任务经
  insight.monthly_plan_replay_inputs、掌握行按 ID），同样的配置下逐字节重建同一份快照。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Awaitable, Callable, Mapping, Sequence

import asyncpg

from .claims import MasteryRow
from .monthly import (EXAM_TYPES, ExamScore, Goal, MonthlyConfig, MonthlyInputs, PlanClaim, RetestDue, TargetScore, TemplateLibrary,
                      build_monthly_plan, month_window, previous_month)
from .policy import Policy
from .shared import Forbidden
from .store import InsightStore, VisibleClaim

LIVE = ("hypothesis", "established")
STAR = ("transfer", "retain")
MasteryByIds = Callable[[str, Sequence[str]], Awaitable[Sequence[MasteryRow]]]


class MonthlyError(Exception):
    """生成不了（不带数据）：kind = unavailable（档案不存在或没有权益，对外同一个回答）/ month_not_current / rejected"""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


@dataclass(frozen=True)
class MonthlyOutcome:
    status: str          # generated / exists
    plan_id: str
    month: date
    generated_at: Any
    inputs_digest: str


async def current_month(conn: asyncpg.Connection) -> date:
    return await conn.fetchval("SELECT date_trunc('month', now() AT TIME ZONE 'Asia/Shanghai')::date")


async def _history(conn: asyncpg.Connection, ids: Sequence[str]) -> tuple[dict[str, str], dict[str, datetime]]:
    """每条判断的最新 claim_history 版本，与门槛首次进入“迁移”（成立或假设）的时刻"""
    latest = {str(r["claim_id"]): str(r["id"]) for r in await conn.fetch(
        """SELECT DISTINCT ON (claim_id) claim_id, id FROM insight.claim_history WHERE claim_id = ANY ($1::uuid[])
            ORDER BY claim_id, at DESC, id DESC""", list(ids))}
    star = {str(r["claim_id"]): r["t"] for r in await conn.fetch(
        """SELECT claim_id, min(at) AS t FROM insight.claim_history
            WHERE claim_id = ANY ($1::uuid[]) AND to_status::text = ANY ($2::text[]) AND value ->> 'gate' = ANY ($3::text[])
            GROUP BY claim_id""", list(ids), list(LIVE), list(STAR))}
    return latest, star


def _plan_claim(v: VisibleClaim, history_id: str, star_at: datetime | None) -> PlanClaim:
    c = v.row
    return PlanClaim(c.id, c.subject, c.target_type, c.target_id, c.template_key, c.template_version, c.status, dict(c.value),
                     tuple(c.visibility), c.source_type, c.expires_at, c.created_at, v.text_template, history_id, star_at)


async def plan_claims(store: InsightStore, student: str) -> tuple[PlanClaim, ...]:
    seen: dict[str, VisibleClaim] = {}
    for audience in ("student", "guardian"):
        for v in await store.visible_claims(student, audience, target_type="node", statuses=LIVE, limit=1000):
            seen.setdefault(v.row.id, v)
    for v in await store.visible_claims(student, "guardian", target_type="exam", statuses=LIVE, limit=200):
        seen.setdefault(v.row.id, v)
    async with store.pool.acquire() as conn:
        latest, star = await _history(conn, list(seen))
    return tuple(sorted((_plan_claim(v, latest.get(cid, ""), star.get(cid)) for cid, v in seen.items()), key=lambda c: c.claim_id))


def _goals(rows: Sequence[Mapping[str, Any]]) -> tuple[Goal, ...]:
    out = []
    for g in rows or []:
        targets = tuple(TargetScore(t["subject"], float(t["full_score"]), float(t["target_score"])) for t in g.get("target_scores") or [])
        out.append(Goal(str(g["goal_id"]), tuple(g.get("focus_subjects") or ()), targets))
    return tuple(out)


def _retests(rows: Sequence[Mapping[str, Any]]) -> tuple[RetestDue, ...]:
    return tuple(RetestDue(r["subject"], r["node_id"], date.fromisoformat(r["date"]), str(r["task_id"])) for r in rows or [])


_SCORE_COLS = ("id::text AS id, subject::text AS subject, exam_type, exam_date, full_score::float8 AS full_score, "
               "raw_score::float8 AS raw_score")


def _score(r: Mapping[str, Any]) -> ExamScore:
    return ExamScore(r["id"], r["subject"], r["exam_type"], r["exam_date"], r["full_score"], r["raw_score"])


async def latest_scores(conn: asyncpg.Connection, student: str) -> tuple[ExamScore, ...]:
    """每科最近一次录入的当前成绩（学校考试口径；学生与家长各录一份时取最近录入的那份，待人确认）"""
    rows = await conn.fetch(
        f"""SELECT DISTINCT ON (subject) {_SCORE_COLS}
              FROM insight.exam_record
             WHERE student_id = $1::uuid AND raw_score IS NOT NULL AND superseded_at IS NULL
               AND exam_date <= (now() AT TIME ZONE 'Asia/Shanghai')::date AND exam_type = ANY ($2::text[])
             ORDER BY subject, exam_date DESC, created_at DESC, id""", student, list(EXAM_TYPES))
    return tuple(_score(r) for r in rows)


async def exam_plans(conn: asyncpg.Connection, student: str, month: date) -> tuple[str, ...]:
    start, end = month_window(month)
    return tuple(r["id"] for r in await conn.fetch(
        """SELECT id::text AS id FROM insight.exam_record
            WHERE student_id = $1::uuid AND raw_score IS NULL AND superseded_at IS NULL AND exam_date >= $2::date AND exam_date < $3::date
            ORDER BY id""", student, start.date(), end.date()))


async def _mastery_rows(mastery: Any, student: str) -> tuple[MasteryRow, ...]:
    all_rows = getattr(mastery, "all_rows", None)
    if all_rows is None:
        return ()
    return tuple(await all_rows(student))


def _outcome(status: str, row: Mapping[str, Any]) -> MonthlyOutcome:
    return MonthlyOutcome(status, str(row["id"]), date.fromisoformat(str(row["month"])), row["generated_at"], str(row["inputs_digest"]))


async def generate_monthly_plan(store: InsightStore, student: str, month: date, policy: Policy, cfg: MonthlyConfig,
                                templates: TemplateLibrary, forbidden: Forbidden, mastery: Any) -> MonthlyOutcome:
    """生成并落库本月的月度计划。月份不是本月 → month_not_current；已有本月快照 → 直接返回（status = exists，不写新行）；
    档案不存在或没有权益 → unavailable（不读其他输入、不写行）；库里守卫拒收 → rejected"""
    async with store.pool.acquire() as conn:
        if month != await current_month(conn):
            raise MonthlyError("month_not_current")
        existing = await conn.fetchval("SELECT insight.monthly_plan_read($1::uuid, $2::date)", student, month)
        if existing is not None:
            return _outcome("exists", existing)
        facts = await conn.fetchval("SELECT insight.monthly_plan_facts($1::uuid, $2::date)", student, month)
        if not facts or facts.get("entitled") is not True:
            raise MonthlyError("unavailable")
        as_of = await conn.fetchval("SELECT now()")
        prev = await conn.fetchval("SELECT insight.monthly_plan_read($1::uuid, $2::date)", student, previous_month(month))
        scores = await latest_scores(conn, student)
        plans = await exam_plans(conn, student, month)
    inputs = MonthlyInputs(student=student, month=month, as_of=as_of, mastery=await _mastery_rows(mastery, student),
                           claims=await plan_claims(store, student), goals=_goals(facts.get("goals")), latest_scores=scores,
                           exam_plans=plans, retests_due=_retests(facts.get("retests_due")),
                           prev_plan=prev["student_plan"] if prev else None, prev_plan_id=str(prev["id"]) if prev else None)
    plan = build_monthly_plan(inputs, policy, cfg, templates, forbidden)
    try:
        async with store.pool.acquire() as conn:
            saved = await conn.fetchval("SELECT insight.save_monthly_plan($1::uuid, $2::date, $3::jsonb)", student, month, plan)
            row = await conn.fetchval("SELECT insight.monthly_plan_read($1::uuid, $2::date)", student, month)
    except (asyncpg.ObjectNotInPrerequisiteStateError, asyncpg.InsufficientPrivilegeError):
        raise MonthlyError("unavailable") from None        # 读输入之后权益变了或档案删了：同样不写
    except asyncpg.CheckViolationError:
        raise MonthlyError("rejected") from None           # 守卫拒收（例如生成之后判断又变了）：不写，调用方可重试
    return _outcome("generated" if saved.get("created") else "exists", row)


async def read_plan(store: InsightStore, student: str, month: date) -> Mapping[str, Any] | None:
    async with store.pool.acquire() as conn:
        return await conn.fetchval("SELECT insight.monthly_plan_read($1::uuid, $2::date)", student, month)


# ---------------------------------------------------------------- 重放

_REPLAY_CLAIM_SQL = """
SELECT c.id::text AS id, c.subject::text AS subject, c.target_type::text AS target_type, c.target_id, c.template_key, c.template_version,
       c.source_type::text AS source_type, c.created_at, t.text_template, h.id::text AS history_id, h.to_status::text AS status, h.value,
       h.visibility, h.expires_at,
       (SELECT min(x.at) FROM insight.claim_history x
         WHERE x.claim_id = c.id AND x.at <= h.at AND x.to_status::text = ANY ($3::text[]) AND x.value ->> 'gate' = ANY ($4::text[])) AS star_at
  FROM insight.claim_history h
  JOIN insight.claim c ON c.id = h.claim_id
  JOIN insight.claim_template t ON t.template_key = c.template_key AND t.version = c.template_version
 WHERE c.student_id = $1::uuid AND h.id = ANY ($2::uuid[])
"""


async def db_mastery_by_ids(pool: Any) -> MasteryByIds:
    """掌握行按 ID 取回（platform.mastery_estimate 的节点键行；行本身只追加，更替只改 is_current）"""
    async def load(student: str, ids: Sequence[str]) -> Sequence[MasteryRow]:
        async with pool.acquire() as conn:
            recs = await conn.fetch(
                """SELECT id::text AS id, student_id::text AS student_id, subject::text AS subject, node_id, cognitive_level,
                          state::text AS state, p_mastery::float8 AS p_mastery, confidence::float8 AS confidence,
                          evidence_ids::text[] AS evidence_ids, algorithm_version::text AS algorithm_version, computed_at, next_retest_at
                     FROM platform.mastery_estimate WHERE student_id = $1::uuid AND id = ANY ($2::uuid[]) AND node_id IS NOT NULL""",
                student, list(ids))
        return tuple(MasteryRow(r["id"], r["student_id"], r["subject"], r["node_id"], r["cognitive_level"], r["state"], r["p_mastery"],
                                float(r["confidence"]), tuple(r["evidence_ids"] or ()), r["algorithm_version"], r["computed_at"],
                                r["next_retest_at"]) for r in recs)
    return load


async def replay_inputs(store: InsightStore, student: str, month: date, mastery_by_ids: MasteryByIds) -> tuple[MonthlyInputs, Mapping[str, Any]]:
    """按快照的 input_refs 重建生成时的输入；返回（输入, 落库的快照）。没有快照抛 MonthlyError(unavailable)"""
    async with store.pool.acquire() as conn:
        row = await conn.fetchval("SELECT insight.monthly_plan_read($1::uuid, $2::date)", student, month)
        if row is None:
            raise MonthlyError("unavailable")
        refs = row["input_refs"]
        extra = await conn.fetchval("SELECT insight.monthly_plan_replay_inputs($1::uuid, $2::date)", student, month)
        recs = await conn.fetch(_REPLAY_CLAIM_SQL, student, [h for _, h in refs["claims"]], list(LIVE), list(STAR))
        scores = await conn.fetch(f"SELECT {_SCORE_COLS} FROM insight.exam_record WHERE student_id = $1::uuid AND id = ANY ($2::uuid[])",
                                  student, refs["exam_records"])
        prev = None
        if refs["prev_plan_id"] is not None:
            prev = await conn.fetchval("SELECT insight.monthly_plan_read($1::uuid, $2::date)", student, previous_month(month))
            if prev is None or str(prev["id"]) != refs["prev_plan_id"]:
                raise MonthlyError("rejected")
    claims = tuple(PlanClaim(r["id"], r["subject"], r["target_type"], r["target_id"], r["template_key"], int(r["template_version"]),
                             r["status"], dict(r["value"] or {}), tuple(r["visibility"]), r["source_type"], r["expires_at"], r["created_at"],
                             r["text_template"], r["history_id"], r["star_at"]) for r in recs)
    if len(claims) != len(refs["claims"]):
        raise MonthlyError("rejected")  # 引用的判断已不在（例如档案删除）：重放不了
    inputs = MonthlyInputs(student=student, month=month, as_of=datetime.fromisoformat(refs["as_of"]),
                           mastery=tuple(await mastery_by_ids(student, refs["mastery"])), claims=claims,
                           goals=_goals(extra.get("goals")), latest_scores=tuple(_score(r) for r in scores),
                           exam_plans=tuple(refs["exam_plans"]), retests_due=_retests(extra.get("retests_due")),
                           prev_plan=prev["student_plan"] if prev else None, prev_plan_id=refs["prev_plan_id"])
    return inputs, row


__all__: Sequence[str] = ("MonthlyError", "MonthlyOutcome", "generate_monthly_plan", "read_plan", "plan_claims", "latest_scores",
                          "replay_inputs", "db_mastery_by_ids")
