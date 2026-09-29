"""合并窗口（spec 设计要点 3；KGE 第四章“发布”“分级发布闸门”，V1.1 修订 7）。

- 编译器产出以“一次合并请求”为单位。
- 低风险：按日（北京时间）聚合成一个合并请求；每个页面每天最多发布一次——同一天的多次低风险编译合并成一次，
  当天已发布过的页面顺延到下一天的窗口。
- 中、高风险：每个编译结果单独成一个合并请求，标注审核人数：高风险两人（结构性变更两人确认，类别待 ADR 12 #2）；
  阶段 0 没有语义体检，中风险按高风险处理，也是两人（与 release-gate-sprint4 #7 一致）。
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

from ..pages.format import serialize
from .rebuild import CompileResult, basis_atoms, bump

REVIEWERS = {"low": 0, "medium": 2, "high": 2}


@dataclass(frozen=True)
class MergeRequest:
    mr_id: str
    kind: str  # daily_low | single
    day: dt.date
    risk: str
    reviewers_required: int
    results: tuple[CompileResult, ...]
    notes: tuple[str, ...] = ()

    @property
    def page_ids(self) -> list[str]:
        return [r.page_id for r in self.results]


def combine(first: CompileResult, later: CompileResult) -> CompileResult:
    """同一页面同一天的两次低风险编译合并成一次发布：旧版取前一次的旧版，新版取后一次的新版，版本号只升一次"""
    version = bump(first.old_page.page_version, "low", "render")
    new_page = later.new_page.with_header(page_version=version)
    reasons = tuple(dict.fromkeys(first.reasons + later.reasons))
    changed = tuple(dict.fromkeys(first.changed_sections + later.changed_sections))
    return replace(later, old_page=first.old_page, old_text=first.old_text, new_page=new_page, new_text=serialize(new_page),
                   reasons=reasons, changed_sections=changed, basis=basis_atoms(first.old_page, new_page))


def plan_window(results: Iterable[CompileResult], published: Mapping[str, Iterable[dt.date]] | None = None) -> list[MergeRequest]:
    """published：页面 ID → 已经发布过低风险改动的日期（每页每天最多一次）"""
    done = {pid: set(days) for pid, days in (published or {}).items()}
    changed = sorted((r for r in results if not r.unchanged), key=lambda r: (r.compiled_at, r.page_id))
    slots: dict[dt.date, dict[str, CompileResult]] = {}
    deferred: dict[dt.date, list[str]] = {}
    singles: list[MergeRequest] = []
    for r in changed:
        if r.risk != "low":
            reviewers = REVIEWERS[r.risk]
            notes = ("高风险：必须教研审核，两人确认",) if r.risk == "high" else ("中风险：阶段 0 无语义体检，按高风险处理，两人确认",)
            singles.append(MergeRequest(f"mr-{r.day.isoformat()}-{r.page_id}-{r.risk}-{r.job.job_id[3:11]}", "single", r.day, r.risk,
                                        reviewers, (r,), notes))
            continue
        day = r.day
        while day in done.get(r.page_id, set()):
            day += dt.timedelta(days=1)
        if day != r.day:
            deferred.setdefault(day, []).append(r.page_id)
        slot = slots.setdefault(day, {})
        slot[r.page_id] = combine(slot[r.page_id], r) if r.page_id in slot else r
    mrs = []
    for day in sorted(slots):
        notes = ["低风险：程序体检通过即可合并，事后按比例抽检；每页每天最多发布一次"]
        if deferred.get(day):
            notes.append("当天已发布过、顺延到本窗口的页面：" + "、".join(sorted(set(deferred[day]))))
        items = tuple(slots[day][pid] for pid in sorted(slots[day]))
        mrs.append(MergeRequest(f"mr-{day.isoformat()}-low", "daily_low", day, "low", 0, items, tuple(notes)))
    return sorted(mrs + singles, key=lambda m: (m.day, m.kind != "daily_low", m.mr_id))
