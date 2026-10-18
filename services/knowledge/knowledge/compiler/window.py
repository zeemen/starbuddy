"""合并窗口（spec 设计要点 3；KGE 第四章“发布”“分级发布闸门”，V1.1 修订 7）。

- 编译器产出以“一次合并请求”为单位。
- 低风险：按日（北京时间）聚合成一个合并请求；每个页面每天最多发布一次——同一天的多次低风险编译合并成一次，
  当天已发布过的页面顺延到下一天的窗口。
- 中、高风险：每个编译结果单独成一个合并请求，标注审核人数：高风险两人（结构性变更两人确认，类别待 ADR 12 #2）；
  阶段 0 没有语义体检，中风险按高风险处理，也是两人（与 release-gate-sprint4 #7 一致）。
  KGE-11 的规则式语义体检与审核台分流建议（review_console.risk.suggest_review_level，返回 medium_spot_check）**尚未生效**：
  REVIEWERS 里没有 medium_spot_check，中风险仍两人；建议级别与这里的映射说明见 risk.SUGGESTED_REVIEWERS（学习服务测试核对）。
- 对账失败的页面（specs/release-gate-sprint4 第 6 节）：审核台的页面对账（`python -m review_console reconcile-pages`，
  报告格式 kge-pages-reconcile/1）列出 held_pages，合并窗口跳过这些页面的一切改动，直到人处理、对账重新通过。
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from ..nodeid import is_node_id
from ..pages.format import serialize
from .rebuild import CompileResult, basis_atoms, bump

REVIEWERS = {"low": 0, "medium": 2, "high": 2}  # 与审核台对账 review_console/pages.py 的 REQUIRED 相等（学习服务测试核对）
HOLD_FORMAT = "kge-pages-reconcile/1"
# 对账报告的有效期缺省一个合并窗口周期（每天一次）：报告里的 exported_at（所依据的托管平台导出时刻）早于此即视为过期
DEFAULT_HOLD_MAX_AGE = dt.timedelta(hours=24)
HOLD_FUTURE_SKEW = dt.timedelta(minutes=5)
MAX_HOLD_REPORT_BYTES = 10 * 1024 * 1024


def load_hold(raw: Any, *, now: dt.datetime, max_age: dt.timedelta = DEFAULT_HOLD_MAX_AGE) -> frozenset[str]:
    """从页面对账报告取“合并窗口跳过的页面”。格式不对、自相矛盾（ok 与 held_pages 不一致）、过期（exported_at 早于
    now - max_age）或时间在将来，一律 ValueError，不当作没有失败。"""
    if not isinstance(raw, dict) or raw.get("format") != HOLD_FORMAT:
        raise ValueError(f"对账报告的 format 必须是 {HOLD_FORMAT}")
    held, ok = raw.get("held_pages"), raw.get("ok")
    if not isinstance(held, list) or not isinstance(ok, bool) or any(not isinstance(p, str) or not is_node_id(p) for p in held):
        raise ValueError("对账报告的 held_pages / ok 写法不对")
    if ok != (not held):
        raise ValueError("对账报告自相矛盾：ok 与 held_pages 不一致")
    try:
        exported = dt.datetime.fromisoformat(str(raw.get("exported_at")).replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("对账报告缺少有效的 exported_at") from None
    if exported.tzinfo is None or now.tzinfo is None:
        raise ValueError("对账报告与当前时刻都必须带时区")
    if exported < now - max_age:
        raise ValueError(f"对账报告已过期（所依据的合并记录导出早于 {max_age} 之前），请重新导出并对账")
    if exported > now + HOLD_FUTURE_SKEW:
        raise ValueError("对账报告的导出时刻在将来")
    return frozenset(held)


def read_hold_report(path: str, *, now: dt.datetime, max_age: dt.timedelta = DEFAULT_HOLD_MAX_AGE) -> frozenset[str]:
    """读对账报告文件（最大 10 MB）并取 held_pages。"""
    import json
    from pathlib import Path
    data = Path(path).read_bytes()
    if len(data) > MAX_HOLD_REPORT_BYTES:
        raise ValueError("对账报告超过 10 MB")
    return load_hold(json.loads(data.decode("utf-8")), now=now, max_age=max_age)


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


def plan_window(results: Iterable[CompileResult], published: Mapping[str, Iterable[dt.date]] | None = None,
                held: Iterable[str] = ()) -> list[MergeRequest]:
    """published：页面 ID → 已经发布过低风险改动的日期（每页每天最多一次）；
    held：对账失败、合并窗口要跳过的页面 ID（load_hold 读出），这些页面不进任何合并请求（任何风险级别）"""
    done = {pid: set(days) for pid, days in (published or {}).items()}
    hold = frozenset(held)
    changed = sorted((r for r in results if not r.unchanged and r.page_id not in hold), key=lambda r: (r.compiled_at, r.page_id))
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
