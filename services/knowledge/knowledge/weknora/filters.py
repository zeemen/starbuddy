"""过滤怎么落到 WeKnora（KGE-12 C；ADR 16 第 3.2 节“先过滤后检索”）：做成可替换的策略，默认策略等技术验证 V07 定。

WeKnora 的检索接口只能按知识库、文档 ID、标签过滤（技术验证计划 V07）。可选做法：
- **允许清单**（本文件，现在唯一实现）：按交付清单里的元数据先算出符合条件的文档 ID，检索时只传这些 ID；
- 组合标签、分库：等 V07 在真实实例上比较后再加（同一个接口）。
不管哪种策略，检索结果回来后都要再核对一次：命中的文档必须在本次允许的集合里（`admits`），不在的一律丢掉并计数，
不相信 WeKnora 一定按条件过滤（模拟 WeKnora 的 ignore_filters 缺陷就是测这个）。

条目上缺的维度（例如试卷题没有出版社、学期）视为“不适用”，不排除；请求给了考试年份、地区、类型或节点时，条目必须有且相等
（lessons 待人确认第 5 条）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Protocol, Sequence

from .manifest import DeliveryManifest, Entry

MAX_ALLOWLIST = 2000   # 一次检索最多传给 WeKnora 的文档 ID 数（V07 核对请求大小后再调）
REQUIRED = ("subject", "grade", "province")
NOT_APPLICABLE_WHEN_MISSING = ("publisher", "edition", "semester")
EXACT_WHEN_GIVEN = ("exam_year", "exam_region", "exam_type")


class FilterTooBroad(ValueError):
    """符合条件的文档超过 MAX_ALLOWLIST：不做检索（不退化成全库检索）"""


@dataclass(frozen=True)
class SearchPlan:
    knowledge_ids: tuple[str, ...]
    kb_ids: tuple[str, ...]
    tag_ids: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.knowledge_ids and not self.tag_ids


class FilterStrategy(Protocol):
    name: str

    def plan(self, filters: Mapping, content_types: Sequence[str], *, exclude: Iterable[str] = ()) -> SearchPlan: ...

    def admits(self, plan: SearchPlan, weknora_id: str | None) -> Entry | None: ...


def matches(e: Entry, filters: Mapping, content_types: Sequence[str]) -> bool:
    if not e.active or e.content_type not in content_types:
        return False
    for k in REQUIRED:
        if k in filters and getattr(e, k) != filters[k]:
            return False
    for k in NOT_APPLICABLE_WHEN_MISSING:
        if k in filters and getattr(e, k) is not None and getattr(e, k) != filters[k]:
            return False
    for k in EXACT_WHEN_GIVEN:
        if k in filters and getattr(e, k) != filters[k]:
            return False
    if filters.get("node_ids") and not set(filters["node_ids"]) & set(e.node_ids):
        return False
    return True


class AllowlistStrategy:
    """按交付清单算允许的文档 ID，检索时只传这些 ID；结果回来再核对一遍"""
    name = "allowlist"

    def __init__(self, manifest: DeliveryManifest, max_ids: int = MAX_ALLOWLIST) -> None:
        self.manifest = manifest
        self.max_ids = max_ids

    def plan(self, filters: Mapping, content_types: Sequence[str], *, exclude: Iterable[str] = ()) -> SearchPlan:
        skip = set(exclude)
        ids = sorted(e.weknora_id for e in self.manifest.entries if e.weknora_id not in skip and matches(e, filters, content_types))
        if len(ids) > self.max_ids:
            raise FilterTooBroad(f"符合条件的文档有 {len(ids)} 个，超过 {self.max_ids}")
        return SearchPlan(knowledge_ids=tuple(ids), kb_ids=self.manifest.kb_ids)

    def admits(self, plan: SearchPlan, weknora_id: str | None) -> Entry | None:
        if weknora_id is None or weknora_id not in plan.knowledge_ids:
            return None
        e = self.manifest.by_weknora_id.get(weknora_id)
        return e if e is not None and e.active else None


STRATEGIES = {"allowlist": AllowlistStrategy}
