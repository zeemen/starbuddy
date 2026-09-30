"""跨科联调回放（spec 设计要点 1）：诊断 → 批改 → 复测，全部经学习服务的契约接口（learning-internal v1 → plugin-api v1.0）。

- 建档（账号、家庭、学生档案、家长绑定、选科）是平台账号侧的准备工作，以超级用户写入测试库，做法同
  services/learning/tests/conftest.py 的 student 夹具；学习数据（证据、作答、判分、观察、错因）只经 /internal/v1/{diagnose,grade} 写入；
- 复测：KGE-04 第二步（retest_task 与复测调度）未做，用 StubRetestScheduler 这个接口桩决定“复测哪道、何时”，
  执行仍经同一个 grade 契约接口（ingest.purpose = retest）。桩产出的每一条复测都标 scheduler = "stub"，证据包里原样标“桩”；
  第二步合入后换成真实调度（实现 RetestScheduler 协议即可），这里的执行与断言不变。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from .cohort import CONTENT_VERSION, RUBRIC, Attempt, Cohort, Step, Student, retest_time, sid, zhixing_answer
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
    scheduler: str | None = None  # 复测：stub（KGE-04 第二步前）


# ---------------- 复测调度（接口 + 桩） ----------------
@dataclass(frozen=True)
class RetestTask:
    subject: str
    item: str
    due_at: str
    source_step: str
    scheduler: str


class RetestScheduler(Protocol):
    """复测调度的接口：给定学生与其批改结果摘要（只有 ID 与编码），返回到期的复测任务。KGE-04 第二步的真实调度实现同一协议。"""

    name: str

    def due(self, student: Student, graded: list[CallRecord]) -> list[RetestTask]: ...


class StubRetestScheduler:
    """接口桩（不是掌握计算）：每名学生每科取第一道“没有答对”的批改题，没有则取第一道批改题，7 天后复测一次。
    只看摘要里的 verdict，不读、不写掌握度；不做任何间隔优化。"""

    name = "stub"

    def due(self, student: Student, graded: list[CallRecord]) -> list[RetestTask]:
        tasks = []
        for subject in ("aya", "zhixing"):
            recs = [r for r in graded if r.subject == subject and r.method == "grade" and r.purpose == "practice" and r.status == 200]
            if not recs:
                continue
            pick = next((r for r in recs if r.summary.get("verdict") != "correct"), recs[0])
            step = next(s for s in student.steps if s.step_id == pick.step_id)
            a = step.attempts[0]
            tasks.append(RetestTask(subject, a.item, retest_time(a.at), pick.step_id, self.name))
        return tasks


def retest_step(student: Student, task: RetestTask, model: ScriptedModel, zx_items: dict[str, dict]) -> Step:
    if task.subject == "aya":
        response = student.retest_answers["aya"][task.item]
    else:
        quality = student.retest_answers["zhixing_quality"]
        text = zhixing_answer(f"{student.label}复测", task.item, quality, zx_items[task.item]["points"])
        model.register("zhixing", task.item, text, student.pattern)
        response = {"text": text}
    return Step(f"retest-{task.source_step}", "grade", task.subject, (Attempt(task.item, response, task.due_at, 0),))


# ---------------- 回放 ----------------
@dataclass
class RunLog:
    calls: list[CallRecord] = field(default_factory=list)
    requests: list[tuple[Student, Step, str, str | None]] = field(default_factory=list)  # 供重跑：(学生, 步骤, 用途, 调度)
    retests: list[dict] = field(default_factory=list)

    def to_jsonable(self) -> dict:
        return {"calls": [asdict(c) for c in self.calls], "retests": self.retests}


async def _post(api, student: Student, step: Step, *, run: int, purpose: str, scheduler: str | None) -> CallRecord:
    body = build_envelope(student, step, run=run, purpose=purpose)
    r = await api.post(f"/internal/v1/{step.method}", json=body)
    ctx = body["request"]["context"]
    return CallRecord(student.label, student.student_id, step.step_id, step.method, step.subject, purpose,
                      ctx["idempotency_key"], ctx["usage_id"], r.status_code, r.headers.get("idempotent-replayed") == "true",
                      r.json() if r.headers.get("content-type", "").startswith("application/") else {}, scheduler)


def register_texts(cohort: Cohort, model: ScriptedModel) -> None:
    for s in cohort.students:
        for step in s.steps:
            if step.method == "grade" and step.subject in ("zhixing", "wenli"):
                a = step.attempts[0]
                model.register(step.subject, a.item, a.response["text"], s.pattern)


async def replay_cohort(api, cohort: Cohort, model: ScriptedModel, scheduler: RetestScheduler | None = None) -> RunLog:
    """第一轮：诊断 → 批改（按学生脚本顺序）→ 复测（调度桩）。"""
    scheduler = scheduler or StubRetestScheduler()
    zx_items = load_zhixing_items()
    register_texts(cohort, model)
    log = RunLog()
    for s in cohort.students:
        mine = []
        for step in s.steps:
            rec = await _post(api, s, step, run=1, purpose="practice", scheduler=None)
            log.calls.append(rec)
            log.requests.append((s, step, "practice", None))
            mine.append(rec)
        if s.pattern == "interrupted":
            continue  # 中断：离开后没有复测
        for task in scheduler.due(s, mine):
            step = retest_step(s, task, model, zx_items)
            rec = await _post(api, s, step, run=1, purpose="retest", scheduler=task.scheduler)
            log.calls.append(rec)
            log.requests.append((s, step, "retest", task.scheduler))
            log.retests.append({"label": s.label, "student_id": s.student_id, "subject": task.subject, "item": task.item,
                                "due_at": task.due_at, "source_step": task.source_step, "scheduler": task.scheduler,
                                "status": rec.status})
    return log


async def rerun(api, log: RunLog) -> list[CallRecord]:
    """整轮重跑：同一批请求体、同一幂等键（request_id 换新，模拟 BFF 重试），应当全部是重放。"""
    out = []
    for s, step, purpose, scheduler in log.requests:
        out.append(await _post(api, s, step, run=2, purpose=purpose, scheduler=scheduler))
    return out
