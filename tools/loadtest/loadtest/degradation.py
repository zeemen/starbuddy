"""单模型失效降级清单（spec 设计要点 2）：每科每个方法用不用模型、用哪类任务与数据级别，三种情形下的期望行为。

期望行为是断言的唯一来源（services/learning/tests/test_degradation.py 逐条断言）。规格未定义之处按默认
“宁可不答不乱答”：不给分、不写证据、不扣次，登记在 specs/load-cost-baseline-sprint5/lessons.md 待人确认。

情形：
  primary_down  主模型失效 → 熔断打开 → 切备选；数据级别过滤不放宽（L2 永远不到境外供应商）；照常出结果；
  all_down      主备都失效 → 用户可见降级；计量不扣次：学习服务 5xx 在 BFF 归为“结果未知”，同键退避重试仍未知则
                不结算、不释放，预留过期后由过期任务回收；之后同键重试只查不执行（replay 查无即封存，前端换新键）；
  recovered     恢复 → 冷却后半开试探回到主模型，熔断关闭。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Policy:
    subject: str
    method: str
    uses_model: bool
    task: str | None
    data_class: str | None
    primary_down: str
    all_down: str
    recovered: str
    status: str  # asserted（端到端断言）| not_applicable（当前实现不用模型）| not_implemented（方法未实现）


POLICIES: tuple[Policy, ...] = (
    Policy("zhixing", "grade", True, "T2", "L2",
           "切备选出结果；model_version 记备选的快照；不到境外供应商",
           "批改不给分、不写任何证据与判分行；学习服务回 5xx，BFF meteredCall 归为结果未知、同键退避重试仍未知 → 不结算不释放，"
           "预留过期回收（不扣次）；过期后同键重试只查不执行；“转人工排队”未实现（lessons 待人确认 #D1）",
           "冷却后半开试探回到主模型，熔断关闭；同一幂等键已封存，学生重交得新键", "asserted"),
    Policy("wenli", "grade", True, "T4", "L2",
           "切备选出结果；model_version 记备选的快照；不到境外供应商",
           "同道法：不给分、不写证据、结果未知不结算不释放、过期回收；客观题不受影响", "同道法", "asserted"),
    Policy("aya", "grade", False, None, None, "不受影响（验证器判分）", "不受影响（验证器判分）", "—", "not_applicable"),
    Policy("aya", "diagnose", False, None, None, "不受影响", "不受影响", "—", "not_applicable"),
    Policy("wenli", "diagnose", False, None, None, "不受影响（客观题）", "不受影响（客观题）", "—", "not_applicable"),
    Policy("zhixing", "diagnose", False, None, None, "不受影响（过程模式）", "不受影响（过程模式）", "—", "not_applicable"),
    Policy("*", "teach", True, "T1", "L2", "（讲解未实现）", "规格默认：固定提示“讲解暂不可用”，不生成内容（lessons #D2）", "—",
           "not_implemented"),
    Policy("*", "diagnose(模型出题)", True, None, None, "（无模型参与的诊断）", "规格默认：暂停可恢复（lessons #D3）", "—",
           "not_implemented"),
)

MODEL_POLICIES = tuple(p for p in POLICIES if p.status == "asserted")

_CALL = re.compile(r"callRoute\(")
_TASK = re.compile(r'task:\s*"(T\d)"')


def adapter_model_calls(root: Path = ROOT) -> dict[str, set[str]]:
    """静态扫描三科适配器源码里经学习服务路由接口调模型的任务类型：{科目: {任务}}（清单完整性检查）"""
    out: dict[str, set[str]] = {}
    for subject in ("aya", "wenli", "zhixing"):
        tasks: set[str] = set()
        for f in sorted((root / "adapters" / subject / "src").rglob("*.ts")):
            text = f.read_text(encoding="utf-8")
            for m in _CALL.finditer(text):
                window = text[m.end(): m.end() + 400]
                tasks.update(_TASK.findall(window))
        out[subject] = tasks
    return out
