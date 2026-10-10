"""跨科有序列表：去重、排序、按预算截断（纯函数，不连库）。PLT-11 spec 范围第 1 条。

- 去重（只在“可直接去做”的条目之间、同一学科同一知识节点上）：
  · 复测列表（retest/queue 的 due 条目）里有的节点，计划里 kind = retest 的任务不再列（只留可直接去做的复测条目）；
  · 再练（practice/queue 的 due 条目）与复测同节点时只留复测。
  计划任务的节点取它的第一个目标节点（BFF 与前端显示的也是这一个）；没有目标节点的任务不参与去重。
- 排序：来源优先级（配置）→ 同一来源内各科保持原有顺序 → 学科轮转（按请求里学科的顺序，每科轮流取一条）。
- 截断：按 estimated_minutes 依次累加，第一条放不进预算的及其之后全部截掉，返回截掉的条数；
  第一条本身就超过预算时仍保留它（列表不会因为一条长任务而整体为空）。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Mapping, Sequence


@dataclass(frozen=True)
class Item:
    subject: str
    kind: str                         # retest / verify / practice / 计划原有的 kind
    node_id: str | None
    estimated_minutes: int
    source: str                       # retest / practice / plan
    entry_id: str | None              # 可去做的条目引用（复测、再练）；计划任务为空
    rationale_code: str | None        # 计划原有的 rationale_code；复测、再练为空

    def view(self) -> dict:
        return asdict(self)


def dedupe(retest: Sequence[Item], practice: Sequence[Item], plan: Sequence[Item]) -> tuple[list[Item], list[Item], list[Item]]:
    covered = {(i.subject, i.node_id) for i in retest if i.node_id is not None}
    kept_plan = [p for p in plan if not (p.kind == "retest" and p.node_id is not None and (p.subject, p.node_id) in covered)]
    kept_practice = [p for p in practice if (p.subject, p.node_id) not in covered]
    return list(retest), kept_practice, kept_plan


def rotate(items: Sequence[Item], subjects: Sequence[str]) -> list[Item]:
    """同一来源内：各科保持原有顺序，学科轮转（按 subjects 的顺序每科轮流取一条）。"""
    per = {s: [i for i in items if i.subject == s] for s in subjects}
    out: list[Item] = []
    k = 0
    while any(k < len(v) for v in per.values()):
        for s in subjects:
            if k < len(per[s]):
                out.append(per[s][k])
        k += 1
    return out


def order(groups: Mapping[str, Sequence[Item]], priority: Iterable[str], subjects: Sequence[str]) -> list[Item]:
    out: list[Item] = []
    for source in priority:
        out += rotate(groups.get(source, ()), subjects)
    return out


def truncate(items: Sequence[Item], budget: int) -> tuple[list[Item], int]:
    kept: list[Item] = []
    total = 0
    for n, i in enumerate(items):
        if kept and total + i.estimated_minutes > budget:
            return kept, len(items) - n
        kept.append(i)
        total += i.estimated_minutes
    return kept, 0


def build(retest: Sequence[Item], practice: Sequence[Item], plan: Sequence[Item], *, priority: Iterable[str],
          subjects: Sequence[str], budget: int) -> tuple[list[Item], int]:
    """去重 → 排序 → 截断。返回（保留的条目, 截掉的条数）。"""
    r, p, pl = dedupe(retest, practice, plan)
    return truncate(order({"retest": r, "practice": p, "plan": pl}, priority, subjects), budget)
