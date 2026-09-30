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
验证任务：KGE-04 第二步（platform.retest_task）落地前用接口桩 StubVerificationScheduler（任务 ID 与题目引用都是确定性的占位，
题目引用带 stub-verify 前缀，**不是真实题目**）；落地后换成真实实现（同一接口）。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol, Sequence

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


class VerificationScheduler(Protocol):
    """验证任务接口（KGE-04 第二步的复测 / 验证任务调度）。schedule 在洞察服务写异议的同一事务里被调用"""

    async def schedule(self, student: str, subject: str, node_id: str, dispute_id: str, count: int) -> list[VerificationTask]: ...

    async def describe(self, subject: str, task_ids: Sequence[str]) -> list[str]: ...


class UnavailableVerificationScheduler:
    """非 test 环境且没有注入真实实现时的缺省：一律不可用（失败即关闭，insight.dispute 的 i_know 回 501）"""

    stub = False
    available = False

    async def schedule(self, student: str, subject: str, node_id: str, dispute_id: str, count: int) -> list[VerificationTask]:  # noqa: ARG002
        raise RuntimeError("verification scheduler is not available")

    async def describe(self, subject: str, task_ids: Sequence[str]) -> list[str]:  # noqa: ARG002
        raise RuntimeError("verification scheduler is not available")


def default_scheduler(env: str) -> "VerificationScheduler":
    """接口桩只给 test 环境（安全审查 F2）；其他环境没有注入真实实现就不可用"""
    return StubVerificationScheduler() if env == "test" else UnavailableVerificationScheduler()


def stub_task_refs(dispute_id: str, count: int) -> list[str]:
    """接口桩为某条异议生成的任务 ID（指标里把接口桩单独计数用）"""
    return [str(uuid.uuid5(STUB_NAMESPACE, f"{dispute_id}:{i}")) for i in range(count)]


class StubVerificationScheduler:
    """接口桩（KGE-04 第二步前）：任务 ID = uuid5(异议 ID, 序号)，题目引用 = `<学科>:stub-verify-<任务 ID 前 12 位>`。
    确定性：同一异议重复请求得到同一组引用。**不产生真实任务、不是真实题目**（lessons 标注）"""

    stub = True
    available = True

    async def schedule(self, student: str, subject: str, node_id: str, dispute_id: str, count: int) -> list[VerificationTask]:  # noqa: ARG002
        out = []
        for i in range(count):
            tid = str(uuid.uuid5(STUB_NAMESPACE, f"{dispute_id}:{i}"))
            out.append(VerificationTask(tid, self._item(subject, tid)))
        return out

    async def describe(self, subject: str, task_ids: Sequence[str]) -> list[str]:
        return [self._item(subject, t) for t in task_ids]

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
