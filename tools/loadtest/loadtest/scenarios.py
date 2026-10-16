"""场景（spec 设计要点 1）：按用户旅程排的步骤，纯数据，不碰网络。

- student：家长登录 → 打开学生模式 → 今日计划 → 一次数学诊断（开始、取题、作答一题、暂停）→ 三道批改（数学、道法、语文）→ 星图；
- parent：家长登录 → 家长首页（孩子列表）→ 孩子的星图、额度、选科；
- meter：计量预留 / 结算并发（同一幂等键两路并发预留、部分结算、重复结算、预留后释放），直接调计量函数（与 BFF 同一组函数、同一角色）。

每一步的“接口标签”是报表里的行名（不含任何 ID）。批改没有 BFF HTTP 入口（壳里还没有批改路由）：经计量驱动桩
（node/meter-harness.mts）直接运行 BFF 的 meteredCall（计量语义与 BFF 相同，耗时含预留、重试与结算），见 README 与 lessons。

可选（PLT-16，--with-practice）：学生旅程在星图之后另走错题再练与复测的列表与取题（PRACTICE_STEPS），用来观察分学科并发闸门；
不开时旅程与请求序列（digest）和以前逐项相同。作答不在场景里（要有到期条目与票据，见 README“已知偏差”）。
"""
from __future__ import annotations

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


def journey_steps(kind: str, practice: bool = False) -> tuple[str, ...]:
    """旅程的步骤；practice 打开时学生旅程另带再练与复测的列表、取题"""
    steps = JOURNEYS[kind]
    return steps + PRACTICE_STEPS if practice and kind == "student" else steps

# 报表行名：接口（方法 + 路径模板），不含 ID
ENDPOINTS: dict[str, str] = {
    "login.otp_request": "BFF POST /auth/otp/request",
    "login.otp_verify": "BFF POST /auth/otp/verify",
    "login.student_session": "BFF POST /students/{id}/student-session",
    "today": "BFF GET /students/{id}/today",
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


def keyed(step: str) -> bool:
    """有幂等键的步骤：写入类（诊断、批改、计量）"""
    return step.startswith(("diagnose.start", "diagnose.answer", "diagnose.pause", "grade.", "meter."))
