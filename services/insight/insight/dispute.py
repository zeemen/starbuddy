"""异议三路（SIE V1.1 第八章；spec 设计要点 1；迁移 0032）：纯函数与验证任务接口，不连库。

- 我其实会（i_know）：判断在同一事务里转“待验证”（0010 状态机，原因 dispute），登记 1–5 道验证任务（配置 verification_items，
  默认 2 道含 1 道变式）；验证结果以 KGE 的掌握行回来（判断生成同一入口 sync_mastery），按 i_know_outcome 结案：
    - 验证门槛高于判断所述 → 学生的异议成立：原判断推翻（verification_refuted），同一事务里按新掌握行重算出新判断（上调）；
    - 与判断所述相同 → 异议不成立：原判断回到成立（verification_supported，需策略已签字；未签字时保持待验证）；
    - 低于判断所述 → 冲突：0032 的流转表没有对应的结案方式，异议保持验证中，判断按常规生命周期走（lessons 待人确认）；
    - 没有异议之后的新证据 → 等待。
- 那次只是失误（one_off_slip）：只有学生本人；只记学生自述（异议行本身，来源 self），不改判断、不产生任何标签；观察期满结案。
- 数据有误（data_error）：带原始事实引用（fact_kind + fact_id），进 0023 统一复核队列；原始事实在原录入通道更正后，
  相关判断撤回并按判断生成重算（settle_fact_corrections）；核实无误则结案维持。
验证任务：真实实现 LearningVerificationScheduler（KGE-04 第二步 PR 3）调学习服务 `POST /internal/v1/verification`
（learning-internal 1.1.0，洞察服务专用令牌），建 platform.retest_task（reason = verify_self_report），只建任务、不改掌握；
非 test 环境配置了学习服务地址与令牌就用它，没配置一律不可用（i_know 回 501）。接口桩 StubVerificationScheduler 只给 test 环境
（任务 ID 与题目引用都是确定性的占位，题目引用带 stub-verify 前缀，**不是真实题目**），指标里与真实任务分开计数。
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol, Sequence

import httpx

from .claims import MasteryRow
from .shared import GATE_RANK

KINDS = ("i_know", "one_off_slip", "data_error")
NEXT_STEP = {"i_know": "verification_scheduled", "one_off_slip": "observe_followups", "data_error": "open_fact_for_correction"}
FACT_KINDS = ("attempt", "exam_record", "upload")
ACTIVE = ("open", "verifying")
# 0032 insight.dispute_step_allowed 的流转表（有测试逐行核对迁移文件）
STEPS: frozenset[tuple[str, str | None, str, str | None]] = frozenset({
    ("i_know", None, "open", None), ("i_know", "open", "verifying", None),
    ("i_know", "verifying", "resolved_supported", "verification_supported"),
    ("i_know", "verifying", "resolved_unsupported", "verification_not_supported"),
    ("i_know", "open", "rejected", "claim_no_longer_visible"), ("i_know", "open", "withdrawn", "withdrawn_by_raiser"),
    ("i_know", "verifying", "withdrawn", "withdrawn_by_raiser"),
    ("one_off_slip", None, "open", None), ("one_off_slip", "open", "resolved_unsupported", "self_statement_recorded"),
    ("one_off_slip", "open", "rejected", "claim_no_longer_visible"), ("one_off_slip", "open", "withdrawn", "withdrawn_by_raiser"),
    ("data_error", None, "verifying", None), ("data_error", "verifying", "fact_corrected", "fact_corrected"),
    ("data_error", "verifying", "resolved_unsupported", "data_confirmed"),
    ("data_error", "verifying", "rejected", "claim_no_longer_visible"), ("data_error", "verifying", "withdrawn", "withdrawn_by_raiser"),
})
STUB_NAMESPACE = uuid.UUID("5b1e0000-0000-4000-8000-00000000d15a")


def step_allowed(kind: str, from_status: str | None, to_status: str, code: str | None) -> bool:
    return (kind, from_status, to_status, code) in STEPS


def fact_ref(kind: str, fact_id: str) -> str:
    """契约 fact_ref：`<kind>:<id>`"""
    if kind not in FACT_KINDS:
        raise ValueError("unknown fact kind")
    return f"{kind}:{fact_id}"


def verify_by(created_at: datetime, hours: int) -> datetime:
    """验证题的完成期限（契约 verify_by）：异议提出后 verify_within_hours 小时（默认 24，指标“24 小时内推送验证题”）"""
    return created_at + timedelta(hours=hours)


@dataclass(frozen=True)
class VerificationTask:
    task_id: str      # 验证任务引用（0032 verification_task_refs；KGE-04 第二步 platform.retest_task.id）
    item_ref: str     # 契约 ItemRef（subject:item_id）


class VerificationUnavailable(Exception):
    """学习服务确定不能为这个单元建验证任务（学科没有掌握配置、单元不在当前地图里）：不重试，异议整笔回滚"""


class VerificationScheduler(Protocol):
    """验证任务接口（KGE-04 第二步的复测 / 验证任务调度）。schedule 在洞察服务写异议的同一事务里被调用；
    student 由调用方从异议记录（insight.dispute.student_id）取，cognitive_level 取判断 value 里的层次"""

    async def schedule(self, student: str, subject: str, node_id: str, dispute_id: str, count: int, *,
                       cognitive_level: str | None = None) -> list[VerificationTask]: ...

    async def describe(self, subject: str, task_ids: Sequence[str]) -> list[str]: ...

    async def cancel(self, student: str, task_ids: Sequence[str]) -> int:
        """补偿：洞察服务写异议的事务在 schedule 之后回滚时，尽力撤销刚建的任务；返回撤销失败的条数（不抛出）"""
        ...


class UnavailableVerificationScheduler:
    """非 test 环境且没有配置学习服务（也没有注入实现）时的缺省：一律不可用（失败即关闭，insight.dispute 的 i_know 回 501）"""

    stub = False
    available = False

    async def schedule(self, student: str, subject: str, node_id: str, dispute_id: str, count: int, *,  # noqa: ARG002
                       cognitive_level: str | None = None) -> list[VerificationTask]:
        raise RuntimeError("verification scheduler is not available")

    async def describe(self, subject: str, task_ids: Sequence[str]) -> list[str]:  # noqa: ARG002
        raise RuntimeError("verification scheduler is not available")

    async def cancel(self, student: str, task_ids: Sequence[str]) -> int:  # noqa: ARG002
        return 0


def task_item_ref(subject: str, task_id: str) -> str:
    """真实验证任务在契约 verification_item_refs（ItemRef）里的写法：`<学科>:verify-task-<任务 ID 32 位十六进制>`。
    题目由编排层经知识服务 item.similar 按任务的节点与层次选（学习服务不返回题目内容）；在那之前这里只能给任务引用，
    不是真实题目 ID（洞察服务契约差异，lessons PR 3 待人确认）"""
    return f"{subject}:verify-task-{uuid.UUID(task_id).hex}"


@dataclass
class LearningVerificationScheduler:
    """真实实现：调学习服务 `POST /internal/v1/verification`（learning-internal 1.1.0，insightServiceToken）。
    - 学生来自调用方（Sie03Store 从异议记录取），学习服务只接受洞察服务令牌，BFF 与客户端没有途径指定学生；
    - 每个单元（节点 × 层次）一条任务（retest_task 每单元每种原因最多一条 open），count 作为 item_count 交给选题；
    - 学习服务 409 MASTERY_UNAVAILABLE → VerificationUnavailable（不重试）；其他失败 → RuntimeError（整笔回滚，按 5xx 处理）；
    - 超时：连接 2 秒、单次读写 5 秒（httpx.Timeout(5.0, connect=2.0)），另加整次调用总时长 5 秒（asyncio.wait_for）——
      调用发生在洞察服务写异议的事务里，不能让学生锁与行锁被拖住；超时按失败处理（整笔回滚 + 补偿撤销）；
    - cancel：事务回滚后的补偿，调 `/internal/v1/verification/cancel`，失败只计数、不抛出；
    - 不跟随重定向、不读代理环境变量；日志不记学生与异议 ID"""
    base_url: str
    token: str
    transport: Any = None
    total_timeout: float = 5.0
    stub: bool = field(default=False, init=False)
    available: bool = field(default=True, init=False)

    async def schedule(self, student: str, subject: str, node_id: str, dispute_id: str, count: int, *,
                       cognitive_level: str | None = None) -> list[VerificationTask]:
        if cognitive_level is None:
            raise VerificationUnavailable("claim has no cognitive level")
        body = {"request_id": str(uuid.uuid4()), "dispute_ref": str(uuid.UUID(dispute_id)), "student_ref": str(uuid.UUID(student)),
                "subject": subject, "node_id": node_id, "cognitive_level": cognitive_level, "reason": "verify_self_report",
                "item_count": int(count)}
        r = await self._post("/internal/v1/verification", body)
        if r.status_code == 409:
            raise VerificationUnavailable("learning service cannot schedule verification for this unit")
        if r.status_code != 200:
            raise RuntimeError(f"learning verification returned {r.status_code}")
        t = r.json()
        if (not isinstance(t, dict) or t.get("node_id") != node_id or t.get("cognitive_level") != cognitive_level
                or not isinstance(t.get("task_id"), str)):
            raise RuntimeError("learning verification response does not match the request")
        return [VerificationTask(str(uuid.UUID(t["task_id"])), task_item_ref(subject, t["task_id"]))]

    async def describe(self, subject: str, task_ids: Sequence[str]) -> list[str]:
        return [task_item_ref(subject, t) for t in task_ids]

    async def cancel(self, student: str, task_ids: Sequence[str]) -> int:
        failed = 0
        for t in task_ids:
            try:
                r = await self._post("/internal/v1/verification/cancel",
                                     {"request_id": str(uuid.uuid4()), "task_id": str(uuid.UUID(t)), "student_ref": str(uuid.UUID(student))})
                ok = r.status_code == 200
            except Exception:  # noqa: BLE001 — 补偿尽力而为：不让它盖住原来的错误
                ok = False
            failed += 0 if ok else 1
        return failed

    async def _post(self, path: str, body: dict) -> httpx.Response:
        async def go() -> httpx.Response:
            async with httpx.AsyncClient(transport=self.transport, trust_env=False, follow_redirects=False,
                                         timeout=httpx.Timeout(5.0, connect=2.0)) as c:
                return await c.post(f"{self.base_url}{path}", json=body, headers={"authorization": f"Bearer {self.token}"})
        try:
            return await asyncio.wait_for(go(), self.total_timeout)
        except asyncio.TimeoutError:
            raise RuntimeError("learning verification call timed out") from None


def default_scheduler(env: str, learning: tuple[str, str] | None = None) -> "VerificationScheduler":
    """接口桩只给 test 环境（安全审查 F2）；其他环境配置了学习服务（地址, 令牌）就用真实实现，没配置一律不可用"""
    if env == "test":
        return StubVerificationScheduler()
    if learning is not None:
        return LearningVerificationScheduler(*learning)
    return UnavailableVerificationScheduler()


def stub_task_refs(dispute_id: str, count: int) -> list[str]:
    """接口桩为某条异议生成的任务 ID（指标里把接口桩单独计数用）"""
    return [str(uuid.uuid5(STUB_NAMESPACE, f"{dispute_id}:{i}")) for i in range(count)]


class StubVerificationScheduler:
    """接口桩（KGE-04 第二步前）：任务 ID = uuid5(异议 ID, 序号)，题目引用 = `<学科>:stub-verify-<任务 ID 前 12 位>`。
    确定性：同一异议重复请求得到同一组引用。**不产生真实任务、不是真实题目**（lessons 标注）"""

    stub = True
    available = True

    async def schedule(self, student: str, subject: str, node_id: str, dispute_id: str, count: int, *,  # noqa: ARG002
                       cognitive_level: str | None = None) -> list[VerificationTask]:
        out = []
        for i in range(count):
            tid = str(uuid.uuid5(STUB_NAMESPACE, f"{dispute_id}:{i}"))
            out.append(VerificationTask(tid, self._item(subject, tid)))
        return out

    async def describe(self, subject: str, task_ids: Sequence[str]) -> list[str]:
        return [self._item(subject, t) for t in task_ids]

    async def cancel(self, student: str, task_ids: Sequence[str]) -> int:  # noqa: ARG002 — 接口桩没有真实任务
        return 0

    @staticmethod
    def _item(subject: str, task_id: str) -> str:
        return f"{subject}:stub-verify-{task_id.replace('-', '')[:12]}"


def i_know_outcome(claim_gate: str | None, claim_evidence: Sequence[str], disputed_at: datetime, row: MasteryRow | None) -> str:
    """“我其实会”的验证结论：supported（异议成立）/ not_supported（维持原判断）/ conflict（验证低于判断所述）/ pending（还没有新证据）"""
    if row is None or row.computed_at <= disputed_at or not (set(row.evidence_ids) - set(claim_evidence)):
        return "pending"
    if claim_gate not in GATE_RANK:
        return "pending"
    got, said = GATE_RANK[row.gate], GATE_RANK[claim_gate]
    if got > said:
        return "supported"
    if got == said:
        return "not_supported"
    return "conflict"
