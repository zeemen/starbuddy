"""跨科联调回放（spec 设计要点 1）：诊断 → 批改 → 复测，全部经学习服务的契约接口（learning-internal v1 → plugin-api v1.0）。

- 建档（账号、家庭、学生档案、家长绑定、选科）是平台账号侧的准备工作，以超级用户写入测试库，做法同
  services/learning/tests/conftest.py 的 student 夹具；学习数据（证据、作答、判分、观察、错因）只经学习服务的写入路径：诊断与批改经
  /internal/v1/{diagnose,grade}（BFF 服务令牌），复测经进程内的同一个写入服务（LT-04：直接经 HTTP 的用途只能是 practice，复测用途只由
  可信调用方按票据写入）；写入前的计量记录（预留、自练计次）以超级用户经测试库的 learningtest.provision_meter 代建（联调不经 BFF）；
- 复测：由 KGE-04 第二步的真实调度驱动（RetestTaskScheduler）：学习服务在 grade 落库后同步重算掌握、同一事务里建
  platform.retest_task；这里以学习服务身份读该生开着的复测轮任务（reason = scheduled / early），把时钟推进到任务的 due_at
  确认到期（learning.mastery.store.due_tasks），按任务的节点与层次选一道该生没做过的题，执行仍经同一个 grade 契约接口
  （ingest.purpose = retest）。每条复测标 scheduler = "retest_task"。合成配置下到不了复测（没有任务）时不执行任何复测，
  原因由 runner 的 retest_via_retest_task 断言给出（不编造复测）。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from learning import meter as METER
from learning.errors import IngestError
from learning.mastery import store as MS
from learning.store import learning_tx

from .cohort import CONTENT_VERSION, RUBRIC, Attempt, Cohort, Step, Student, sid, zhixing_answer
from .mastery import ITEMS_OF_NODE
from .scripted import ScriptedModel

ROOT = Path(__file__).resolve().parents[3]


def load_zhixing_items() -> dict[str, dict]:
    pack = json.loads((ROOT / "adapters/zhixing/content/pack.json").read_text(encoding="utf-8"))
    return {it["id"]: it for it in pack["items"]}


# ---------------- 建档（平台账号侧准备，非学习数据） ----------------
async def seed_accounts(admin, cohort: Cohort) -> None:
    async with admin.acquire() as c, c.transaction():
        guardians = sorted({s.guardian_id for s in cohort.students} | {cohort.unlinked_guardian_id})
        await c.executemany("INSERT INTO platform.account (id, kind) VALUES ($1::uuid, 'guardian') ON CONFLICT DO NOTHING",
                            [(g,) for g in guardians])
        await c.executemany("INSERT INTO platform.account (id, kind) VALUES ($1::uuid, 'student') ON CONFLICT DO NOTHING",
                            [(s.account_id,) for s in cohort.students])
        households = sorted({(s.household_id, s.guardian_id) for s in cohort.students})
        await c.executemany("INSERT INTO platform.household (id, created_by_account) VALUES ($1::uuid, $2::uuid) ON CONFLICT DO NOTHING",
                            households)
        for s in cohort.students:
            await c.execute(
                """INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month)
                   VALUES ($1::uuid, $2::uuid, $3::uuid, $4, 'nanjing', 'bubian-2024', $5::text::date) ON CONFLICT DO NOTHING""",
                s.student_id, s.account_id, s.household_id, s.grade, s.birth_year_month)
            await c.execute(
                """INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via)
                   SELECT $1::uuid, $2::uuid, 'household_creator'
                    WHERE NOT EXISTS (SELECT 1 FROM platform.guardian_student_link WHERE guardian_account_id = $1::uuid AND student_id = $2::uuid)""",
                s.guardian_id, s.student_id)
            # 两份同意（合成文本版本）：0059 起学习服务只处理有有效同意的学生（platform.student_processing_allowed，LT-06）
            await c.execute(
                """INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type, document_version, decision)
                   SELECT $2::uuid, $1::uuid, t, replace(t, '_', '-') || '@synthetic-0', 'granted'
                     FROM unnest(ARRAY['privacy_policy', 'guardian_consent_under14']) t
                    WHERE NOT EXISTS (SELECT 1 FROM platform.consent_record r WHERE r.student_id = $2::uuid AND r.consent_type = t)""",
                s.guardian_id, s.student_id)
            await c.executemany(
                "INSERT INTO platform.subject_enrollment (student_id, subject) VALUES ($1::uuid, $2::platform.subject) ON CONFLICT DO NOTHING",
                [(s.student_id, subj) for subj in s.enrollment])


# ---------------- 请求拼装（契约形状） ----------------
def _attempt(a: Attempt, attempt_id: str, subject: str) -> dict:
    return {"attempt_id": attempt_id, "item_ref": f"{subject}:{a.item}", "occurred_at": a.at, "response": a.response,
            "elapsed_sec": 60, "hint_level_used": a.hint_level_used}


def build_envelope(student: Student, step: Step, *, run: int, purpose: str = "practice") -> dict:
    tag = (student.label, step.step_id)
    ctx = {"request_id": sid("request", *tag, run), "contract_version": "1.0.0", "subject": step.subject,
           "student_ref": student.student_id, "data_class": "L2", "usage_id": sid("usage", *tag),
           "idempotency_key": sid("key", *tag), "content_version": dict(CONTENT_VERSION[step.subject])}
    attempts = [_attempt(a, sid("attempt", *tag, i), step.subject) for i, a in enumerate(step.attempts)]
    if step.method == "grade":
        request = {"context": ctx, "attempt": attempts[0], "rubric_version": RUBRIC[step.subject]}
    else:
        request = {"context": ctx, "mode": step.mode or "intake", "attempts": attempts}
    ingest = {"channel": "keypad"}
    if purpose != "practice":
        ingest["purpose"] = purpose
    return {"request": request, "ingest": ingest}


@dataclass
class CallRecord:
    label: str
    student_id: str
    step_id: str
    method: str
    subject: str
    purpose: str
    idempotency_key: str
    usage_id: str
    status: int
    replayed: bool
    summary: dict
    scheduler: str | None = None  # 复测：retest_task（KGE-04 第二步真实调度）


# ---------------- 复测调度（KGE-04 第二步：platform.retest_task） ----------------
@dataclass(frozen=True)
class RetestTask:
    subject: str
    item: str
    due_at: str
    source_step: str          # retest_task:<轮次>（复测由哪条任务驱动）
    scheduler: str
    node_id: str = ""
    cognitive_level: str = ""
    rung: str = ""
    reason: str = ""


class RetestScheduler(Protocol):
    """复测调度：给定学生与其批改结果摘要，返回到期的复测任务（只有 ID 与编码）。"""

    name: str

    async def due(self, student: Student, graded: list[CallRecord]) -> list[RetestTask]: ...


class RetestTaskScheduler:
    """真实调度（KGE-04 第二步）：以学习服务身份读该生开着的复测轮任务，把注入时钟推进到 due_at 确认到期，
    按任务的节点选一道该生在本轮脚本里没做过的题（同节点另一题族；没有可选的题就不执行，计入 no_item）。
    不读、不写掌握度；任务的关闭由学习服务在复测作答落库后的重算里完成。"""

    name = "retest_task"

    def __init__(self, pool) -> None:
        self.pool = pool
        self.no_item = 0

    async def due(self, student: Student, graded: list[CallRecord]) -> list[RetestTask]:  # noqa: ARG002
        done = {a.item for st in student.steps for a in st.attempts}
        out: list[RetestTask] = []
        async with learning_tx(self.pool) as conn:
            rows = await conn.fetch(
                """SELECT id::text, subject::text, node_id, cognitive_level, rung, reason, due_at FROM platform.retest_task
                    WHERE student_id = $1::uuid AND status = 'open' AND reason IN ('scheduled', 'early') ORDER BY due_at, id""",
                student.student_id)
            for task_id, subject, node, level, rung, reason, due_at in rows:
                clock = due_at  # 注入时钟推进到 due_at：以它为“当下”，任务应当到期
                if task_id not in {t["id"] for t in await MS.due_tasks(conn, student.student_id, subject, clock)}:
                    continue
                pick = next((i for i in ITEMS_OF_NODE.get(node, []) if i not in done), None)
                if pick is None:
                    self.no_item += 1
                    continue
                done.add(pick)
                out.append(RetestTask(subject, pick, due_at.isoformat(), f"retest_task:{rung}", self.name, node, level, rung, reason))
        return out


def retest_step(student: Student, task: RetestTask, model: ScriptedModel, zx_items: dict[str, dict]) -> Step:
    if task.subject == "aya":
        response = student.retest_answers["aya"][task.item]
    else:
        quality = student.retest_answers["zhixing_quality"]
        text = zhixing_answer(f"{student.label}复测", task.item, quality, zx_items[task.item]["points"])
        model.register("zhixing", task.item, text, student.pattern)
        response = {"text": text}
    # 步骤 ID 进幂等键派生：按任务的节点与轮次区分（同一学生可有多条复测）
    return Step(f"retest-{task.node_id}-{task.rung}", "grade", task.subject, (Attempt(task.item, response, task.due_at, 0),))


# ---------------- 回放 ----------------
@dataclass
class RunLog:
    calls: list[CallRecord] = field(default_factory=list)
    requests: list[tuple[Student, Step, str, str | None]] = field(default_factory=list)  # 供重跑：(学生, 步骤, 用途, 调度)
    retests: list[dict] = field(default_factory=list)

    def to_jsonable(self) -> dict:
        return {"calls": [asdict(c) for c in self.calls], "retests": self.retests}


async def _meter(admin, ctx: dict, method: str, *, counted: bool) -> None:
    """计量（LT-04）：学习服务写入前核对计量账本。联调不经 BFF，以超级用户经测试库里的 learningtest.provision_meter 代建 BFF 那一步：
    练习与诊断是一笔预留中的预留（BFF meteredCall），复测是一行题库自练计次（BFF meter_practice）。已有记录时什么都不做（重跑不新增）。"""
    async with admin.acquire() as c:
        await c.execute("SELECT learningtest.provision_meter($1::uuid, $2, $3::uuid, $4, $5)",
                        ctx["usage_id"], ctx["idempotency_key"], ctx["student_ref"], method, counted)


async def _post(st, student: Student, step: Step, *, run: int, purpose: str, scheduler: str | None) -> CallRecord:
    """st：联调栈（stack.Stack）。练习与诊断经 HTTP（BFF 服务令牌）写；复测的用途只由可信调用方按票据写入，直接经 HTTP 只能是 practice
    （LT-04 决定记录（续）第 3 条），所以复测经进程内的同一个写入服务（IngestService，与学习服务里复测模块的路径相同）写。"""
    body = build_envelope(student, step, run=run, purpose=purpose)
    ctx = body["request"]["context"]
    if purpose == "practice":
        await _meter(st.admin, ctx, step.method, counted=False)
        r = await st.api.post(f"/internal/v1/{step.method}", json=body)
        status, replayed = r.status_code, r.headers.get("idempotent-replayed") == "true"
        summary = r.json() if r.headers.get("content-type", "").startswith("application/") else {}
    else:
        await _meter(st.admin, ctx, step.method, counted=True)
        try:
            summary, replayed = await st.ingest.ingest(step.method, body, "1.0.0", meter=METER.COUNTED)
            status = 200
        except IngestError as e:
            status, replayed, summary = e.status, False, e.problem()
    return CallRecord(student.label, student.student_id, step.step_id, step.method, step.subject, purpose,
                      ctx["idempotency_key"], ctx["usage_id"], status, replayed, summary, scheduler)


def register_texts(cohort: Cohort, model: ScriptedModel) -> None:
    for s in cohort.students:
        for step in s.steps:
            if step.method == "grade" and step.subject in ("zhixing", "wenli"):
                a = step.attempts[0]
                model.register(step.subject, a.item, a.response["text"], s.pattern)


async def replay_cohort(st, cohort: Cohort, model: ScriptedModel, scheduler: RetestScheduler) -> RunLog:
    """第一轮：诊断 → 批改（按学生脚本顺序）→ 复测（retest_task 驱动）。st：联调栈（stack.Stack）。"""
    zx_items = load_zhixing_items()
    register_texts(cohort, model)
    log = RunLog()
    for s in cohort.students:
        mine = []
        for step in s.steps:
            rec = await _post(st, s, step, run=1, purpose="practice", scheduler=None)
            log.calls.append(rec)
            log.requests.append((s, step, "practice", None))
            mine.append(rec)
        if s.pattern == "interrupted":
            continue  # 中断：离开后没有复测
        for task in await scheduler.due(s, mine):
            step = retest_step(s, task, model, zx_items)
            rec = await _post(st, s, step, run=1, purpose="retest", scheduler=task.scheduler)
            log.calls.append(rec)
            log.requests.append((s, step, "retest", task.scheduler))
            log.retests.append({"label": s.label, "student_id": s.student_id, "subject": task.subject, "node_id": task.node_id,
                                "cognitive_level": task.cognitive_level, "rung": task.rung, "reason": task.reason, "item": task.item,
                                "source_step": task.source_step, "scheduler": task.scheduler, "status": rec.status})
    return log


async def rerun(st, log: RunLog) -> list[CallRecord]:
    """整轮重跑：同一批请求体、同一幂等键（request_id 换新，模拟 BFF 重试），应当全部是重放。"""
    out = []
    for s, step, purpose, scheduler in log.requests:
        out.append(await _post(st, s, step, run=2, purpose=purpose, scheduler=scheduler))
    return out
