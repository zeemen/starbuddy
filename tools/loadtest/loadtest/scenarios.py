"""场景（spec 设计要点 1）：按用户旅程排的步骤，纯数据，不碰网络。

- student：家长登录 → 打开学生模式 → 今日计划 → 一次数学诊断（开始、取题、作答一题、暂停）→ 三道批改（数学、道法、语文）→ 星图；
- parent：家长登录 → 家长首页（孩子列表）→ 孩子的星图、额度、选科；
- meter：计量预留 / 结算并发（同一幂等键两路并发预留、部分结算、重复结算、预留后释放），直接调计量函数（与 BFF 同一组函数、同一角色）。

每一步的“接口标签”是报表里的行名（不含任何 ID）。批改没有 BFF HTTP 入口（壳里还没有批改路由）：经计量驱动桩
（node/meter-harness.mts）直接运行 BFF 的 meteredCall（计量语义与 BFF 相同，耗时含预留、重试与结算），见 README 与 lessons。

可选（PLT-16，--with-practice）：学生旅程在星图之后另走错题再练与复测的列表与取题（PRACTICE_STEPS），用来观察分学科并发闸门；
不开时旅程与请求序列（digest）和以前逐项相同。

可选（PLT-20，--journeys，默认全关）：已上线未覆盖的接口按“场景”挂在宿主旅程末尾（EXTRAS；前面各步序号不变），
随宿主旅程的配比进混合负载；多个场景按 EXTRAS 的固定顺序追加（与命令行给的顺序无关），同一种子请求序列可复现。
不开时旅程与请求序列和以前逐项相同。
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

STUDENT_STEPS = (
    "login.otp_request", "login.otp_verify", "login.student_session", "today",
    "diagnose.start", "diagnose.current", "diagnose.answer", "diagnose.pause",
    "grade.aya", "grade.zhixing", "grade.wenli", "star_map",
)
PARENT_STEPS = (
    "login.otp_request", "login.otp_verify", "parent.children", "parent.child_star_map", "parent.child_balance",
    "parent.child_enrollments",
)
METER_STEPS = ("meter.reserve_pair", "meter.settle", "meter.settle_repeat", "meter.reserve", "meter.release")

JOURNEYS: dict[str, tuple[str, ...]] = {"student": STUDENT_STEPS, "parent": PARENT_STEPS, "meter": METER_STEPS}
# 可选的再练与复测步骤（追加在学生旅程末尾，前面各步的序号不变）
PRACTICE_STEPS = ("practice.queue", "practice.next", "retest.queue", "retest.next")

# PLT-20 可选场景：名字 → （宿主旅程、追加的步骤）。顺序即追加顺序。
EXTRAS: dict[str, tuple[str, tuple[str, ...]]] = {
    # 再练与复测作答（走票据）：要求同时打开再练与复测（PRACTICE_STEPS），作答排在它们之后
    "practice_answer": ("student", ("practice.answer", "retest.answer")),
    # 今日计划快照的完成 / 跳过（POST …/events，plan_done / plan_skip）
    "plan_events": ("student", ("plan.done", "plan.skip")),
    "my_plan": ("student", ("my_plan",)),
    "star_map_v1": ("student", ("star_map_v1",)),
    # 诊断续做（同一会话）→ 取当前题 → 探针 → 放弃
    "diagnose_more": ("student", ("diagnose.resume", "diagnose.recheck", "diagnose.probe", "diagnose.abandon")),
    "monthly_plan": ("parent", ("parent.monthly_plan",)),
    "weekly_report": ("parent", ("parent.weekly_report",)),
    "profile_pages": ("parent", ("parent.profile_page",)),
    "prediction": ("parent", ("parent.prediction",)),
}
NEEDS_PRACTICE = frozenset({"practice_answer"})
# 不进 all、只能点名打开的场景：预测（BFF 未接预测客户端，本栈与任何环境都一律 503，只测拒绝路径；契约审查 4）
NOT_IN_ALL = frozenset({"prediction"})
# 开 plan_events 时学生的今日计划另走跨科编排（多一次学习服务调用），步骤另用标签，不与基线 today 比较（契约审查 3）
TODAY_ORCH = "today.orch"


def parse_journeys(text: str | Iterable[str] | None) -> tuple[str, ...]:
    """'a,b' / ['a', 'b'] / 'all'（不含 NOT_IN_ALL；可写 'all,prediction'）→ 按 EXTRAS 顺序排好的场景名；不认识的名字报错；空 → ()"""
    if text is None:
        return ()
    parts = [p.strip() for p in (text.split(",") if isinstance(text, str) else text)]
    parts = [p for p in parts if p]
    if "all" in parts:
        parts = [k for k in EXTRAS if k not in NOT_IN_ALL] + [p for p in parts if p != "all"]
    unknown = sorted(set(parts) - set(EXTRAS))
    if unknown:
        raise ValueError(f"unknown journeys: {unknown}; known: {sorted(EXTRAS)} or 'all'")
    return tuple(k for k in EXTRAS if k in parts)


def journey_steps(kind: str, practice: bool = False, journeys: tuple[str, ...] = ()) -> tuple[str, ...]:
    """旅程的步骤；practice 打开时学生旅程另带再练与复测的列表、取题；journeys 里的场景按 EXTRAS 顺序追加到各自的宿主旅程"""
    steps = JOURNEYS[kind]
    if kind == "student" and "plan_events" in journeys:
        steps = tuple(TODAY_ORCH if s == "today" else s for s in steps)  # 位置不变，只换标签
    if practice and kind == "student":
        steps = steps + PRACTICE_STEPS
    for name, (host, extra) in EXTRAS.items():
        if name in journeys and host == kind:
            steps = steps + extra
    return steps

# 报表行名：接口（方法 + 路径模板），不含 ID
ENDPOINTS: dict[str, str] = {
    "login.otp_request": "BFF POST /auth/otp/request",
    "login.otp_verify": "BFF POST /auth/otp/verify",
    "login.student_session": "BFF POST /students/{id}/student-session",
    "today": "BFF GET /students/{id}/today",
    "today.orch": "BFF GET /students/{id}/today（跨科编排 + 快照，plan_events 场景；不与基线 today 比较）",
    "diagnose.start": "BFF POST /students/{id}/diagnosis/sessions",
    "diagnose.current": "BFF GET /students/{id}/diagnosis/sessions/{sid}",
    "diagnose.answer": "BFF POST …/diagnosis/sessions/{sid}/answer",
    "diagnose.pause": "BFF POST …/diagnosis/sessions/{sid}/pause",
    "grade.aya": "BFF meteredCall → 学习服务 grade（数学，验证器）",
    "grade.zhixing": "BFF meteredCall → 学习服务 grade（道法，模型 T2）",
    "grade.wenli": "BFF meteredCall → 学习服务 grade（语文作文，模型 T4）",
    "star_map": "BFF GET /students/{id}/star-map（学生）",
    "parent.children": "BFF GET /me（家长首页）",
    "parent.child_star_map": "BFF GET /students/{id}/star-map（家长）",
    "parent.child_balance": "BFF GET /students/{id}/balance（家长）",
    "parent.child_enrollments": "BFF GET /students/{id}/enrollments（家长）",
    "practice.queue": "BFF GET /students/{id}/practice/math（再练列表）",
    "practice.next": "BFF POST …/practice/math/entries/{eid}/next（再练取题，经闸门）",
    "retest.queue": "BFF GET /students/{id}/retest/math（复测列表）",
    "retest.next": "BFF POST …/retest/math/entries/{eid}/next（复测取题，经闸门）",
    "practice.answer": "BFF POST …/practice/math/answer（再练作答，票据）",
    "retest.answer": "BFF POST …/retest/math/answer（复测作答，票据）",
    "plan.done": "BFF POST /students/{id}/events（plan_done）",
    "plan.skip": "BFF POST /students/{id}/events（plan_skip）",
    "my_plan": "BFF GET /students/{id}/my-plan（学生）",
    "star_map_v1": "BFF GET /students/{id}/star-map/v1（学生）",
    "diagnose.resume": "BFF POST /students/{id}/diagnosis/sessions（续做）",
    "diagnose.recheck": "BFF GET …/diagnosis/sessions/{sid}（续做后取题）",
    "diagnose.probe": "BFF POST …/diagnosis/sessions/{sid}/probe",
    "diagnose.abandon": "BFF POST …/diagnosis/sessions/{sid}/abandon",
    "parent.monthly_plan": "BFF GET /students/{id}/monthly-plan（家长）",
    "parent.weekly_report": "BFF GET /students/{id}/weekly-report（家长）",
    "parent.profile_page": "BFF GET /students/{id}/profile-pages/math（家长）",
    "parent.prediction": "BFF GET …/exams/{exam_id}/prediction（家长）",
    "meter.reserve_pair": "计量 meter_reserve ×2（同键并发）",
    "meter.settle": "计量 meter_settle",
    "meter.settle_repeat": "计量 meter_settle（重复，幂等）",
    "meter.reserve": "计量 meter_reserve",
    "meter.release": "计量 meter_release",
}

DEFAULT_MIX = (("student", 0.6), ("parent", 0.25), ("meter", 0.15))


@dataclass(frozen=True)
class PlannedRequest:
    arrival: int
    seq: int
    step: str
    key: str | None  # 该步用的幂等键（没有幂等键的读接口为 None）


# PLT-20 新场景里带幂等键的步骤（逐个列出，不用前缀：diagnose.recheck 是读接口）。再练与复测作答的幂等键在票据里（BFF 封装时生成），不在这里
KEYED_EXTRA = frozenset({"diagnose.resume", "diagnose.probe", "diagnose.abandon", "plan.done", "plan.skip"})


def keyed(step: str) -> bool:
    """有幂等键的步骤：写入类（诊断、批改、计量、计划事件）"""
    return step in KEYED_EXTRA or step.startswith(("diagnose.start", "diagnose.answer", "diagnose.pause", "grade.", "meter."))
