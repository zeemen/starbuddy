"""掌握 v0 的写入层（KGE-04 第二步 PR 2，specs/kge-04-mastery-step2/spec.md 第 2 节；设计正文 specs/mastery-estimate-sprint4/spec.md 第 2、7、9、10、11 节）。

recompute(student, subject, node_id, level)，一个单元一个事务（SET LOCAL ROLE starbuddy_learning）：
  1. pg_advisory_xact_lock（键由学生、学科、节点、层次哈希）：同一单元的同步重算、对账、按题重算互相串行；
  2. 重读当前记录与开着的任务；
  3. replay → write_decision → precommit_check（第一步纯函数，算法不改）；
  4. 写入顺序：生成新 ID → 旧记录指向新 ID → 插入新记录；
  5. 同一事务里开、关、撤 retest_task（每单元每种原因最多一条 open；verify_self_report 不经重放，重算不碰）；
  6. 撞唯一索引或更正守卫时整个事务回滚、重试一次，再失败留给对账。
只写节点键的行；没有可用配置（签字或测试环境的合成配置）的学科不写；单元不在当前已发布地图里时：
  retire（撤销）→ 当前记录置 next_retest_at = 当下（待复测）；rename / merge / split → 旧单元不再计算，开着的重放任务撤销。
其他入口：after_ingest（grade / diagnose 落库提交后）、recompute_item（4-G 准入变化）、recompute_map（地图版本切换）、
schedule_verification（SIE-03“我其实会”：只建 verify_self_report 任务，按 dispute_ref 幂等，不改掌握）。
日志只记计数、学科、单元 ID 与错误类名，不记学生 ID 与证据内容（第二步待人确认 #8；规则 8）。
"""
from __future__ import annotations

import json
import logging
import uuid
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

import asyncpg

from ..store import learning_tx
from . import store as S
from .config import MasteryConfig
from .evidence import Unit
from .gates import RUNG_OF_GATE, RetestOutcome, Task, UnitResult, replay
from .write import InvariantViolation, precommit_check, write_decision

log = logging.getLogger("starbuddy.learning.mastery")
REPLAY_REASONS = ("scheduled", "early", "decay", "hint_followup", "verify_in_platform")  # 重算管理的任务；verify_self_report 不在内
VERIFY_NS = uuid.UUID("2f0c8d6e-5a1b-4c3e-9d7f-000000000037")
# 重试一次的 SQLSTATE：23505 = 唯一索引（并发下两次首写撞 mastery_estimate_node_current_uq / retest_task_open_uq）；
# 23514 = check_violation，这里只为更正守卫 check_supersede_target：它在提交时检查“被指向的新记录仍是当前记录”，
# 并发更正时可能因对方先提交而失败，重读后重算即可。其他 CHECK 失败重试一次仍会失败，随后记 failed，不会掩盖错误。
RETRYABLE = ("23505", "23514")


def norm_student(student: str) -> str:
    """学生 ID 规范化（小写、带连字符）：advisory lock 键与自述验证任务 ID 的输入都用它，大小写不同的同一学生不会拿到两把锁。"""
    return str(uuid.UUID(student))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MasteryUnavailable(Exception):
    """学科没有可用配置、没有已发布地图，或单元不在当前地图里：不建任务、不写记录。"""


@dataclass
class MasteryEngine:
    """一个学习服务实例里的掌握重算入口。configs 只含已校验的配置（签字配置，或 LEARNING_ENV=test 的合成配置）。"""
    pool: asyncpg.Pool
    configs: Mapping[str, MasteryConfig]
    clock: Callable[[], datetime] = utcnow
    item_type_of: S.ItemTypeOf = S.no_item_type
    stats: Counter = field(default_factory=Counter)
    # 最近失败的单元（学科, 节点, 层次），不含学生；只留最近 1000 条，常驻进程里不无限增长
    failed_units: deque = field(default_factory=lambda: deque(maxlen=1000))
    # 测试注入点：读完当前记录、写入之前调用（并发测试用它拉长临界区）
    after_read: Callable[[], Awaitable[None]] | None = None

    def config(self, subject: str) -> MasteryConfig | None:
        cfg = self.configs.get(subject)
        return cfg if cfg is not None and cfg.subject == subject else None


@dataclass
class UnitOutcome:
    status: str  # written / unchanged / retired / moved / skipped_no_config / skipped_no_map / failed
    estimate_id: str | None = None
    tasks: Counter = field(default_factory=Counter)
    why: tuple[str, ...] = ()


# ---------- 任务对账（纯函数） ----------
@dataclass
class TaskPlan:
    close: list[tuple[str, str, str]] = field(default_factory=list)   # (任务 ID, passed / failed, 作答 ID)
    cancel: list[str] = field(default_factory=list)
    create: list[Task] = field(default_factory=list)


def diff_tasks(existing: Sequence[S.OpenTask], desired: Sequence[Task], retests: Sequence[RetestOutcome],
               attempt_of: Mapping[str, str], *, keep_rounds: bool = False) -> TaskPlan:
    """开着的任务 vs 重放给出的任务：同原因同轮次同到期的保留；其余旧任务按重放里的复测结果关闭（passed / failed，记作答），
    没有结果的撤销（排程变了：退级、题被撤销、证据被更正）；缺的新建。每种原因最多一条。
    keep_rounds：当前记录保留不动时（见 _compute_and_write），复测轮任务也不动，保证“带 next_retest_at 的当前记录恰有一条任务”。"""
    plan = TaskPlan()
    want = {t.reason: t for t in desired if t.reason in REPLAY_REASONS}
    for reason in REPLAY_REASONS:
        if keep_rounds and reason in ("scheduled", "early"):
            continue
        have = [t for t in existing if t.reason == reason]
        d = want.get(reason)
        keep = None
        if d is not None:
            keep = next((t for t in have if t.rung == d.rung and t.due_at == d.due_at), None)
        for t in have:
            if t is keep:
                continue
            o = next((o for o in retests if o.reason == t.reason and o.rung == t.rung and o.due_at == t.due_at
                      and o.evidence_id in attempt_of), None)
            if o is not None and reason in ("scheduled", "early"):
                plan.close.append((t.id, o.outcome, attempt_of[o.evidence_id]))
            else:
                plan.cancel.append(t.id)
        if d is not None and keep is None:
            plan.create.append(d)
    return plan


# ---------- 库操作 ----------
async def _lock(conn, student: str, subject: str, unit: Unit) -> None:
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                       f"platform.mastery:{student}:{subject}:{unit.node_id}:{unit.cognitive_level}")


async def _write_estimate(conn, *, student: str, subject: str, unit: Unit, map_version_id: str | None, prev_id: str | None,
                          state: str, p: float, confidence: float, algorithm_version: str, evidence_ids: Sequence[str],
                          next_retest_at: datetime | None) -> str:
    new_id = str(uuid.uuid4())
    if prev_id is not None:
        await conn.execute(
            """UPDATE platform.mastery_estimate SET is_current = false, superseded_by = $2::uuid
                WHERE id = $1::uuid AND is_current AND student_id = $3::uuid""",
            prev_id, new_id, student)
    await conn.execute(
        """INSERT INTO platform.mastery_estimate (id, student_id, subject, node_id, cognitive_level, map_version_id, state,
             p_mastery, confidence, algorithm_version, evidence_ids, next_retest_at)
           VALUES ($1::uuid, $2::uuid, $3::platform.subject, $4, $5, $6::uuid, $7::platform.mastery_state, $8::text::numeric,
                   $9::text::numeric, $10, $11::text[]::uuid[], $12::timestamptz)""",
        new_id, student, subject, unit.node_id, unit.cognitive_level, map_version_id, state, f"{p:.6f}", f"{confidence:.6f}",
        algorithm_version, list(evidence_ids), next_retest_at)
    return new_id


async def _sync_tasks(conn, *, student: str, subject: str, unit: Unit, plan: TaskPlan, estimate_id: str | None) -> Counter:
    c: Counter = Counter()
    for task_id, status, attempt_id in plan.close:
        await conn.execute("""UPDATE platform.retest_task SET status = $2, closing_attempt_id = $3::uuid
                               WHERE id = $1::uuid AND student_id = $4::uuid""", task_id, status, attempt_id, student)
        c[f"tasks_{status}"] += 1
    for task_id in plan.cancel:
        await conn.execute("UPDATE platform.retest_task SET status = 'cancelled' WHERE id = $1::uuid AND student_id = $2::uuid",
                           task_id, student)
        c["tasks_cancelled"] += 1
    for t in plan.create:
        ref = estimate_id if t.reason in ("scheduled", "early") else None
        if t.reason in ("scheduled", "early") and ref is None:
            raise InvariantViolation("retest round without a current estimate")
        await conn.execute(
            """INSERT INTO platform.retest_task (student_id, subject, node_id, cognitive_level, rung, reason, due_at,
                 scheduled_by_estimate_id)
               VALUES ($1::uuid, $2::platform.subject, $3, $4, $5, $6, $7::timestamptz, $8::uuid)""",
            student, subject, unit.node_id, unit.cognitive_level, t.rung, t.reason, t.due_at, ref)
        c["tasks_created"] += 1
    return c


@dataclass
class _Work:
    """一个单元在事务里算出的结果：写不写记录、任务怎么对账。任务对账另起一步（见 recompute）。"""
    outcome: UnitOutcome
    plan: TaskPlan | None = None


async def _compute_and_write(engine: MasteryEngine, conn, student: str, subject: str, unit: Unit, cfg: MasteryConfig,
                             ctx: S.MapContext, now: datetime, pre: S.StudentEvidence | None = None) -> _Work:
    await _lock(conn, student, subject, unit)
    cur = await S.current_estimate(conn, student, subject, unit)
    existing = await S.open_tasks(conn, student, subject, unit)
    if engine.after_read is not None:
        await engine.after_read()

    if not ctx.unit_ok(unit.node_id, unit.cognitive_level):
        return await _not_live(engine, conn, student, subject, unit, ctx, cur, existing, now, pre)

    # 同一批单元共用一次读出的证据（审查意见 1）；单独重算或重试时在锁内重读
    ev = pre if pre is not None else await S.load_student(conn, student, subject, ctx, engine.item_type_of)
    rep = replay(ev.for_unit(unit), unit, cfg, now)
    write, why = write_decision(None if cur is None else cur.stored, rep.result, cfg)
    estimate_id = None if cur is None else cur.id
    outcome = UnitOutcome("unchanged", estimate_id, why=why)
    if write:
        r = rep.result
        assert r.state is not None
        precommit_check(r, ev.facts())  # 迁移 / 保持只引用 A 级、已准入、formal、不待复核的证据；不满足就拒绝写入并报警
        estimate_id = await _write_estimate(
            conn, student=student, subject=subject, unit=unit, map_version_id=ctx.current_id,
            prev_id=None if cur is None else cur.id, state=r.state, p=r.p_mastery, confidence=r.confidence,
            algorithm_version=r.algorithm_version, evidence_ids=r.evidence_ids, next_retest_at=r.next_retest_at)
        outcome = UnitOutcome("written", estimate_id, why=why)
    outcome.estimate_id = estimate_id
    # 已有记录、但这次重放没有任何计入证据（例如判分被改回待复核）：记录写不出新行（evidence_ids 至少一条），原样保留，
    # 它的复测轮任务也原样保留；其余任务照常对账。计数 stale_estimate，留给人看（lessons 待人确认）。
    stale = cur is not None and rep.result.state is None
    if stale:
        outcome.tasks["stale_estimate"] += 1  # 随计数进 engine.stats 与对账输出（对账有它就非零退出）
    return _Work(outcome, diff_tasks(existing, rep.tasks, rep.retests, ev.attempt_of(), keep_rounds=stale))


async def _not_live(engine: MasteryEngine, conn, student: str, subject: str, unit: Unit, ctx: S.MapContext,
                    cur: S.CurrentEstimate | None, existing: Sequence[S.OpenTask], now: datetime,
                    pre: S.StudentEvidence | None = None) -> _Work:
    """单元不在当前地图里。撤销（retire）的单元：当前记录置 next_retest_at = 当下（待复测，KGE 第五章），门槛不变、星不熄灭；
    改名、合并、拆分（以及其他不再有效的单元）：旧单元不再计算，开着的重放任务撤销，记录原样保留（是历史）。"""
    status = "moved"
    if cur is not None:
        _, st = S.resolve_node(ctx, cur.map_version_id, unit.node_id)
        if st == "retired":
            status = "retired"
    if status == "moved" or cur is None or cur.stored.state not in RUNG_OF_GATE:
        # 未到执行的记录没有复测轮可排，撤销后只撤任务
        return _Work(UnitOutcome(status, None if cur is None else cur.id), diff_tasks(existing, (), (), {}))
    estimate_id = cur.id
    due = cur.stored.next_retest_at
    out = UnitOutcome("retired", estimate_id)
    if due is None or due > now:
        rows = pre.rows if pre is not None else await S.load_rows(conn, student, subject)
        facts = {r.evidence_id: S.facts_of(r) for r in rows}
        s = cur.stored
        precommit_check(UnitResult(unit.node_id, unit.cognitive_level, s.state, s.p_mastery, s.confidence, now,
                                   s.algorithm_version, cur.evidence_ids), facts)
        estimate_id = await _write_estimate(
            conn, student=student, subject=subject, unit=unit, map_version_id=cur.map_version_id, prev_id=cur.id,
            state=s.state, p=s.p_mastery, confidence=s.confidence, algorithm_version=s.algorithm_version,
            evidence_ids=cur.evidence_ids, next_retest_at=now)
        due = now
        out = UnitOutcome("retired", estimate_id, why=("retired",))
    out.estimate_id = estimate_id
    return _Work(out, diff_tasks(existing, (Task("scheduled", RUNG_OF_GATE[cur.stored.state], due),), (), {}))


def _unit_log(event: str, subject: str, unit: Unit, err: BaseException | None = None) -> None:
    rec: dict[str, Any] = {"event": event, "subject": subject, "node_id": unit.node_id, "cognitive_level": unit.cognitive_level}
    if err is not None:
        rec["error_class"] = type(err).__name__[:48]
    log.warning(json.dumps(rec, ensure_ascii=False))


async def recompute(engine: MasteryEngine, student: str, subject: str, node_id: str, cognitive_level: str,
                    *, now: datetime | None = None,
                    preloaded: tuple[S.MapContext, S.StudentEvidence] | None = None) -> UnitOutcome:
    """重算一个单元。失败不抛出：记计数与单元 ID，返回 failed（调用方照常返回，之后由对账补上）。
    preloaded：同一学生一批单元共用的地图与证据（只用于第一次尝试；重试时在锁内重读）。"""
    unit = Unit(node_id, cognitive_level)
    student = norm_student(student)
    cfg = engine.config(subject)
    if cfg is None:
        engine.stats["skipped_no_config"] += 1
        return UnitOutcome("skipped_no_config")
    now = now or engine.clock()
    for attempt in (1, 2):
        try:
            async with learning_tx(engine.pool) as conn:
                pre = preloaded if attempt == 1 else None
                ctx = pre[0] if pre is not None else await S.load_map(conn, subject)
                if ctx.current_id is None:
                    engine.stats["skipped_no_map"] += 1
                    return UnitOutcome("skipped_no_map")
                work = await _compute_and_write(engine, conn, student, subject, unit, cfg, ctx, now,
                                                None if pre is None else pre[1])
                if work.plan is not None:
                    work.outcome.tasks.update(await _sync_tasks(conn, student=student, subject=subject, unit=unit,
                                                                plan=work.plan, estimate_id=work.outcome.estimate_id))
            engine.stats[work.outcome.status] += 1
            for k, v in work.outcome.tasks.items():
                engine.stats[k] += v
            return work.outcome
        except asyncpg.PostgresError as e:
            if attempt == 1 and (e.sqlstate or "") in RETRYABLE:
                engine.stats["retries"] += 1
                continue
            err: BaseException = e
        except InvariantViolation as e:
            engine.stats["invariant_violations"] += 1
            err = e
        except Exception as e:  # noqa: BLE001 — 重算失败不影响已落库的证据与接口返回（第一步 #6）
            err = e
        engine.stats["failed"] += 1
        engine.failed_units.append((subject, unit.node_id, unit.cognitive_level))
        _unit_log("mastery_recompute_failed", subject, unit, err)
        return UnitOutcome("failed")
    raise AssertionError("unreachable")


async def recompute_units(engine: MasteryEngine, student: str, subject: str, units: Iterable[Unit],
                          *, now: datetime | None = None,
                          preloaded: tuple[S.MapContext, S.StudentEvidence] | None = None) -> Counter:
    c: Counter = Counter()
    for u in sorted(set(units), key=lambda u: (u.node_id, u.cognitive_level)):
        o = await recompute(engine, student, subject, u.node_id, u.cognitive_level, now=now, preloaded=preloaded)
        c["units"] += 1
        c[o.status] += 1
        c.update(o.tasks)
    return c


async def _student_units(engine: MasteryEngine, student: str, subject: str, *, attempt_ids: Sequence[str] | None = None,
                         with_records: bool = False) -> tuple[set[Unit], tuple[S.MapContext, S.StudentEvidence] | None]:
    """一个学生一科的证据只读一次（审查意见 1）：返回涉及的单元与这份地图、证据，供同一批单元共用。"""
    student = norm_student(student)
    async with learning_tx(engine.pool) as conn:
        ctx = await S.load_map(conn, subject)
        if ctx.current_id is None:
            return set(), None
        ev = await S.load_student(conn, student, subject, ctx, engine.item_type_of)
        units = ev.units(attempt_ids)
        if with_records:
            for r in await conn.fetch(
                    """SELECT DISTINCT node_id, cognitive_level FROM platform.mastery_estimate
                        WHERE student_id = $1::uuid AND subject = $2::platform.subject AND is_current AND node_id IS NOT NULL
                       UNION
                       SELECT DISTINCT node_id, cognitive_level FROM platform.retest_task
                        WHERE student_id = $1::uuid AND subject = $2::platform.subject AND status = 'open'
                          AND reason <> 'verify_self_report'""", student, subject):
                units.add(Unit(r[0], r[1]))
    return units, (ctx, ev)


async def after_ingest(engine: MasteryEngine, student: str, subject: str, attempt_ids: Sequence[str],
                       *, now: datetime | None = None) -> Counter:
    """grade / diagnose 落库事务提交后另起事务，同步重算这次作答涉及的单元（只按主节点，第一步 #16）。
    调用方（service._recompute_mastery）另有时间预算，超时交给对账。"""
    if engine.config(subject) is None:
        engine.stats["skipped_no_config"] += 1
        return Counter(skipped_no_config=1)
    engine.stats["triggered"] += 1
    units, pre = await _student_units(engine, student, subject, attempt_ids=attempt_ids)
    return await recompute_units(engine, student, subject, units, now=now, preloaded=pre)


async def recompute_student(engine: MasteryEngine, student: str, subject: str, *, now: datetime | None = None) -> Counter:
    """一个学生一科的全部单元：证据涉及的、已有当前记录的、有开着的重放任务的。"""
    if engine.config(subject) is None:
        return Counter(skipped_no_config=1)
    units, pre = await _student_units(engine, student, subject, with_records=True)
    return await recompute_units(engine, student, subject, units, now=now, preloaded=pre)


async def recompute_item(engine: MasteryEngine, subject: str, item_id: str, *, now: datetime | None = None) -> Counter:
    """4-G 题目准入变化时调用：重算做过这道题的学生、这道题涉及的单元。"""
    c: Counter = Counter()
    if engine.config(subject) is None:
        return Counter(skipped_no_config=1)
    async with learning_tx(engine.pool) as conn:
        rows = await conn.fetch(
            """SELECT student_id::text, array_agg(attempt_id::text) FROM platform.attempt
                WHERE subject = $1::platform.subject AND item_id = $2 GROUP BY student_id ORDER BY student_id""",
            subject, item_id)
    for student, attempts in rows:
        units, pre = await _student_units(engine, student, subject, attempt_ids=list(attempts))
        c.update(await recompute_units(engine, student, subject, units, now=now, preloaded=pre))
        c["students"] += 1
    return c


async def recompute_map(engine: MasteryEngine, subject: str, from_version: str, to_version: str,
                        *, now: datetime | None = None) -> Counter:
    """地图版本切换（4-G / KGE-01 发布流程调用）：to_version 必须是当前已发布版本。按 id_migration 迁移后，
    重算证据或当前记录涉及旧版本的学生的全部单元（每个单元一个事务）。"""
    if engine.config(subject) is None:
        return Counter(skipped_no_config=1)
    async with learning_tx(engine.pool) as conn:
        cur = await conn.fetchval(
            "SELECT id::text FROM kge.map_version WHERE subject = $1::platform.subject AND status = 'published'", subject)
        if cur != to_version:
            raise MasteryUnavailable("to_version is not the published map version")
        students = [r[0] for r in await conn.fetch(
            """SELECT student_id::text FROM platform.evidence_event
                WHERE subject = $1::platform.subject AND map_version_id = $2::uuid
               UNION
               SELECT student_id::text FROM platform.mastery_estimate
                WHERE subject = $1::platform.subject AND is_current AND map_version_id = $2::uuid
               ORDER BY 1""", subject, from_version)]
    c: Counter = Counter()
    for s in students:
        c.update(await recompute_student(engine, s, subject, now=now))
        c["students"] += 1
    return c


async def schedule_verification(engine: MasteryEngine, student: str, subject: str, node_id: str, cognitive_level: str,
                                reason: str, dispute_ref: str, *, now: datetime | None = None) -> dict[str, Any]:
    """SIE-03“我其实会”：只建一条 verify_self_report 任务，不改掌握（只有做题产生的证据会改）。
    同一异议重复调用返回同一条任务（任务 ID 由 dispute_ref 与单元确定）；同一单元已有开着的自述验证任务时返回那一条。
    返回任务 ID、节点与层次（供 item.similar 选题），不含题目内容。"""
    if reason != "verify_self_report":
        raise ValueError("schedule_verification only creates verify_self_report tasks")
    try:
        dispute_ref = str(uuid.UUID(dispute_ref))
    except ValueError:
        raise ValueError("dispute_ref must be a UUID") from None
    if engine.config(subject) is None:
        raise MasteryUnavailable("no mastery configuration for this subject")
    # 学生 ID 规范化后再参与锁键与任务 ID（审查意见 2）。注意：本函数信任调用方给的 student；接 HTTP 或洞察服务时（PR 3），
    # 服务端必须由异议记录（dispute_ref）反查学生，不信请求体里的 student（审查意见 3）
    student = norm_student(student)
    unit = Unit(node_id, cognitive_level)
    task_id = str(uuid.uuid5(VERIFY_NS, f"{dispute_ref}|{student}|{subject}|{node_id}|{cognitive_level}"))
    now = now or engine.clock()
    async with learning_tx(engine.pool) as conn:
        ctx = await S.load_map(conn, subject)
        if not ctx.unit_ok(node_id, cognitive_level):
            raise MasteryUnavailable("unit is not in the published map")
        await _lock(conn, student, subject, unit)
        row = await conn.fetchrow("SELECT id::text, status, due_at FROM platform.retest_task WHERE id = $1::uuid AND "
                                  "student_id = $2::uuid AND reason = 'verify_self_report'", task_id, student)
        created = False
        if row is None:
            row = await conn.fetchrow(
                """SELECT id::text, status, due_at FROM platform.retest_task
                    WHERE student_id = $1::uuid AND subject = $2::platform.subject AND node_id = $3 AND cognitive_level = $4
                      AND reason = 'verify_self_report' AND status = 'open'""", student, subject, node_id, cognitive_level)
        if row is None:
            row = await conn.fetchrow(
                """INSERT INTO platform.retest_task (id, student_id, subject, node_id, cognitive_level, reason, due_at)
                   VALUES ($1::uuid, $2::uuid, $3::platform.subject, $4, $5, 'verify_self_report', $6::timestamptz)
                   RETURNING id::text, status, due_at""", task_id, student, subject, node_id, cognitive_level, now)
            created = True
    engine.stats["verification_created" if created else "verification_reused"] += 1
    return {"task_id": row[0], "node_id": node_id, "cognitive_level": cognitive_level, "status": row[1],
            "due_at": row[2], "created": created}
