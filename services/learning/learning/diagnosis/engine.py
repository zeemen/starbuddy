"""自适应诊断的选题规则与会话状态机（specs/sie-02a-math-diagnosis/spec.md 设计要点 1、2、3）。

纯函数、确定性、不使用任何模型：同样的地图、题目清单与作答结果，选题序列逐字节相同。算法版本 ALGORITHM_VERSION 随会话入库。

选题（aya-diag-trace@0.1.0，待人确认 #3、#4 按默认）：
- 起点：建档年级对应的目标节点（试点只有二次函数主题）；
- 顺序：待测节点里“上层”优先（在强前置图里往下的最长链更长），同层按节点 ID 升序；
- 每个节点出 1 题；答错且该节点还有第 2 题时，再出 1 题确认（confirm），以确认题的结果为准；
- 答对 → 该节点通过，它的全部强前置（传递闭包）记“跳过”，不再出题；
- 答错 → 该节点记未通过，把它的直接强前置加入待测（沿前置向下追溯）；
- 节点没有可用题目 → 记“无题可测”（no_item），不猜，也不再往下追溯；
- 判分结论只读 grade_result.verdict：correct 算对；incorrect、partial 算错；needs_review、not_applicable 记“不明”，不跳过也不追溯。
停止：待测节点全部有结论；或题数、用时到上限（先到先停）。自适应阶段给过程观察任务预留题数与时间。
过程观察（设计要点 3）：自适应结束后从断点附近选最多 3 道 process（写出全过程）+ 1 道 explain（讲给老师听），
优先用本次会话没出过的题；每道题出之前都核对总题数与总用时上限。

断点：未通过、且它的直接强前置都已通过（含跳过）或无题可测的节点。断点只是给 KGE-04 / SIE-02b 的候选，
本任务不算掌握度，也不对学生、家长展示（规则 11）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Protocol

ALGORITHM_VERSION = "aya-diag-trace@0.1.0"

KINDS = ("adaptive", "process", "explain")
REASONS = ("target", "prereq_trace", "confirm", "no_item", "process_near_gap", "explain")


class Source(Protocol):
    """选题需要的公共层信息（知识服务 node.path 只走强前置边；题目清单来自 item.search 或测试夹具）。"""

    map_version: str

    def targets(self, grade: int) -> list[str]: ...
    def prereqs(self, node_id: str) -> list[str]: ...        # 直接强前置
    def ancestors(self, node_id: str) -> frozenset[str]: ...  # 全部强前置（传递闭包）
    def height(self, node_id: str) -> int: ...               # 往下最长强前置链的长度
    def items(self, node_id: str) -> list[str]: ...           # 可用题目，按稳定顺序


@dataclass(frozen=True)
class Params:
    """诊断参数（待人确认 #3、#4）。数值变了要升算法版本。"""
    item_limit: int = 20
    time_budget_sec: int = 2100
    process_count: int = 3
    explain_count: int = 1
    # 出一道新题前按“这道题最多用多久”预留时间，保证累计用时不越过上限（界面上的计时与之一致）
    adaptive_step_sec: int = 90
    process_step_sec: int = 180

    @property
    def reserve_items(self) -> int:
        return min(self.process_count + self.explain_count, self.item_limit // 2)


@dataclass(frozen=True)
class Planned:
    kind: str
    node_id: str
    item_id: str | None
    reason: str


@dataclass(frozen=True)
class Done:
    """已记录的一步（来自 diagnosis_step 与 grade_result）。outcome 只在 status = answered 时有值。"""
    kind: str
    node_id: str
    item_id: str | None
    reason: str
    status: str                  # presented | answered | skipped | no_item
    outcome: str | None = None   # correct | incorrect | unclear
    elapsed_sec: int = 0


def outcome_of(verdict: str | None) -> str:
    if verdict == "correct":
        return "correct"
    if verdict in ("incorrect", "partial"):
        return "incorrect"
    return "unclear"


class HistoryMismatch(Exception):
    """库里的步骤与按同一规则复算的不一致（算法或地图变了）：不能继续这个会话。"""


@dataclass
class Trace:
    status: dict[str, str] = field(default_factory=dict)   # 节点 → pass / fail / skipped / no_item / unclear
    pending_index: int | None = None     # 已出、未作答的那一步在历史里的下标
    next: Planned | None = None          # 下一步（还没入库）
    finished: bool = False
    phase: str = "adaptive"              # adaptive | process | done
    answered: int = 0
    elapsed_sec: int = 0
    break_points: list[str] = field(default_factory=list)
    limited: bool = False                # 自适应阶段因题数或用时上限结束（还有待测节点）


class _Halt(Exception):
    pass


class _Cursor:
    def __init__(self, history: list[Done], trace: Trace) -> None:
        self.h = history
        self.i = 0
        self.t = trace

    def emit(self, p: Planned) -> str | None:
        if self.i < len(self.h):
            d = self.h[self.i]
            if (d.kind, d.node_id, d.item_id, d.reason) != (p.kind, p.node_id, p.item_id, p.reason):
                raise HistoryMismatch(f"step {self.i + 1}")
            self.i += 1
            if d.status == "no_item":
                return None
            if d.status == "answered":
                self.t.answered += 1
                self.t.elapsed_sec += d.elapsed_sec
                return d.outcome
            self.t.pending_index = self.i - 1
            raise _Halt()
        self.t.next = p
        raise _Halt()


def _order(source: Source, nodes: Iterable[str]) -> list[str]:
    return sorted(set(nodes), key=lambda n: (-source.height(n), n))


def break_points(source: Source, status: dict[str, str]) -> list[str]:
    ok = ("pass", "skipped", "no_item")
    return _order(source, [n for n, s in status.items()
                           if s == "fail" and all(status.get(p) in ok for p in source.prereqs(n))])


def trace(source: Source, grade: int, history: list[Done], params: Params = Params()) -> Trace:
    """按规则复算：历史里每一步都必须与规则给出的一致；返回当前待作答的一步或下一步（或已结束）。"""
    t = Trace()
    cur = _Cursor(history, t)
    targets = list(source.targets(grade))
    target_set = set(targets)
    p = params
    cap_items = p.item_limit - p.reserve_items
    reserve_sec = p.reserve_items * p.process_step_sec

    def adaptive_room() -> bool:
        return t.answered < cap_items and t.elapsed_sec + p.adaptive_step_sec + reserve_sec <= p.time_budget_sec

    def process_room() -> bool:
        return t.answered < p.item_limit and t.elapsed_sec + p.process_step_sec <= p.time_budget_sec

    status = t.status
    try:
        # ---------- 自适应阶段 ----------
        pending: set[str] = set(targets)
        while True:
            cand = [n for n in pending if n not in status]
            if not cand:
                break
            if not adaptive_room():
                t.limited = True
                break
            node = _order(source, cand)[0]
            pending.discard(node)
            items = source.items(node)
            if not items:
                cur.emit(Planned("adaptive", node, None, "no_item"))
                status[node] = "no_item"
                continue
            out = cur.emit(Planned("adaptive", node, items[0], "target" if node in target_set else "prereq_trace"))
            if out == "incorrect" and len(items) > 1:
                if adaptive_room():
                    out = cur.emit(Planned("adaptive", node, items[1], "confirm"))
                else:
                    t.limited = True
            if out == "correct":
                status[node] = "pass"
                for a in sorted(source.ancestors(node)):
                    status.setdefault(a, "skipped")
            elif out == "incorrect":
                status[node] = "fail"
                pending.update(q for q in source.prereqs(node) if q not in status)
            else:
                status[node] = "unclear"
        t.break_points = break_points(source, status)

        # ---------- 过程观察 ----------
        t.phase = "process"
        for plan in _process_plan(source, status, t.break_points, targets, history, p):
            if not process_room():
                break
            cur.emit(plan)
    except _Halt:
        t.break_points = break_points(source, status)
        return t
    t.phase = "done"
    t.finished = True
    return t


def _process_plan(source: Source, status: dict[str, str], bps: list[str], targets: list[str], history: list[Done],
                  p: Params) -> list[Planned]:
    """断点附近：断点本身 → 断点已通过的直接前置 → 其余未通过的节点 → （都没有时）通过的目标节点。每个节点先取一道没出过的题。"""
    near: list[str] = list(bps)
    for b in bps:
        near += _order(source, [q for q in source.prereqs(b) if status.get(q) in ("pass", "skipped")])
    near += _order(source, [n for n, s in status.items() if s == "fail" and n not in bps])
    if not near:
        near = _order(source, [n for n in targets if status.get(n) in ("pass", "skipped")])
    seen: list[str] = []
    for n in near:
        if n not in seen:
            seen.append(n)
    used = {d.item_id for d in history if d.kind == "adaptive" and d.item_id}
    picks: list[tuple[str, str]] = []
    want = p.process_count + p.explain_count
    for _ in range(2):  # 第一轮每个节点一道，第二轮再补
        for n in seen:
            for it in source.items(n):
                if it in used or any(it == x for _, x in picks):
                    continue
                picks.append((n, it))
                break
            if len(picks) >= want:
                break
        if len(picks) >= want:
            break
    n_process = min(p.process_count, len(picks))
    if p.explain_count and len(picks) <= p.process_count and len(picks) >= 2:
        n_process = len(picks) - 1  # 题不够时最后一道改为“讲给老师听”，保证有一道讲解任务
    plans = [Planned("process", n, it, "process_near_gap") for n, it in picks[:n_process]]
    plans += [Planned("explain", n, it, "explain") for n, it in picks[n_process:want]]
    return plans


# ---------- 会话状态机（ADR 03：显式状态机） ----------
class State(str, Enum):
    CREATED = "created"
    PROBING = "probing"                 # 已出题，等“能做对吗”（可跳过）
    AWAITING_ANSWER = "awaiting_answer"  # 探针已记，等作答
    GRADING = "grading"                 # 验证器判分中（事务外的一步）
    SELECTING = "selecting"             # 按规则选下一步
    PROCESS_TASKS = "process_tasks"     # 自适应结束，进入过程观察
    PAUSED = "paused"
    FINISHED = "finished"
    ABANDONED = "abandoned"


class Event(str, Enum):
    SELECT = "select"
    PRESENT = "present"
    PROBE = "probe"
    ANSWER = "answer"
    GRADED = "graded"
    ENTER_PROCESS = "enter_process"
    FINISH = "finish"
    PAUSE = "pause"
    RESUME = "resume"
    ABANDON = "abandon"


TRANSITIONS: dict[State, dict[Event, State]] = {
    State.CREATED: {Event.SELECT: State.SELECTING, Event.ABANDON: State.ABANDONED},
    State.SELECTING: {Event.PRESENT: State.PROBING, Event.ENTER_PROCESS: State.PROCESS_TASKS, Event.FINISH: State.FINISHED,
                      Event.PAUSE: State.PAUSED, Event.ABANDON: State.ABANDONED},
    State.PROCESS_TASKS: {Event.PRESENT: State.PROBING, Event.FINISH: State.FINISHED, Event.PAUSE: State.PAUSED,
                          Event.ABANDON: State.ABANDONED},
    # 探针可以跳过：probing 也能直接作答
    State.PROBING: {Event.PROBE: State.AWAITING_ANSWER, Event.ANSWER: State.GRADING, Event.PAUSE: State.PAUSED,
                    Event.ABANDON: State.ABANDONED},
    State.AWAITING_ANSWER: {Event.ANSWER: State.GRADING, Event.PAUSE: State.PAUSED, Event.ABANDON: State.ABANDONED},
    State.GRADING: {Event.GRADED: State.SELECTING},
    State.PAUSED: {Event.RESUME: State.SELECTING, Event.ABANDON: State.ABANDONED},
    State.FINISHED: {},
    State.ABANDONED: {},
}


class InvalidTransition(Exception):
    def __init__(self, state: State, event: Event) -> None:
        super().__init__(f"{event.value} is not allowed in {state.value}")
        self.state = state
        self.event = event


def transition(state: State, event: Event) -> State:
    nxt = TRANSITIONS[state].get(event)
    if nxt is None:
        raise InvalidTransition(state, event)
    return nxt


def derive_state(session_status: str, current: Done | None, probe_recorded: bool) -> State:
    """由库里的会话状态与当前一步推出状态机状态（grading 只存在于一次请求之内，不落库）。"""
    if session_status == "finished":
        return State.FINISHED
    if session_status == "abandoned":
        return State.ABANDONED
    if session_status == "paused":
        return State.PAUSED
    if current is None:
        return State.SELECTING
    return State.AWAITING_ANSWER if probe_recorded else State.PROBING
