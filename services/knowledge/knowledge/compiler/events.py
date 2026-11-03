"""文件式编入事件（KGE-11 范围 2；KGE V1.1“事件驱动自动编入”的第一步）：读原子变更清单与兜底检索信号，产出编译任务清单。

只产出清单：不常驻、不建表（compile_job 入库与调度接入另起，spec 待人确认 #4）、不重建页面、不自动发布。

原子变更清单 kge-atom-changes/1（由导入或审核流程按两个已发布地图快照的差异写出）：
  {"format": "kge-atom-changes/1", "from_map_version": "0.9.0", "to_map_version": "0.9.1",
   "changes": [{"atom": "<type>:<id>", "change": "added | modified | removed", "source_type": "<来源类型>"}]}
  来源类型（KGE V1.1 编入的新入库原子）与它能对应的原子类型（SOURCE_ATOM_TYPES，交叉校验）：
    exam_spec 考试说明 → region、curriculum；past_paper 真题 → region；approved_upload 审核通过的上传题 → region；
    confirmed_pitfall 确认的易错模式 → node、edge；curriculum 课标 → curriculum、node；textbook 教材投影 → projection；
    current_affair 时政 → affair；map_edit 地图编辑 → node、edge。
  地图快照里还没有题目、采分点、易错模式原子：真题、上传题、易错模式目前只是变更的来源标签，编入仍按它们落到的公共层原子算。
  缺省要求清单列全两个快照的差异（漏列即拒收）；--allow-partial 时只编清单列出的原子，summary.unlisted 报漏列数。
  清单里的每一条都必须与实际差异一致（键、变更类型），地图版本也必须对上，否则拒收（清单过期或写错，fail closed）。
  受影响页与风险、优先级一律复用 jobs.plan_jobs：把“旧快照 + 清单里的原子取新快照的版本”作为新快照喂给它；
  列全时结果与 plan_jobs(页面, 旧快照, 新快照) 逐条相同（测试核对）。

兜底检索信号（retrieval.signals 的 JSON Lines，kge-gap-signal/1）作为同一清单的另一来源：按（节点，原因）聚合，
列在清单的 signals 里，不生成渲染重建任务（补页面或全量重编要人发起，一律高风险）。规则 13：聚合后次数与不同问法数
都不低于门槛（缺省 20，待人确认）才列出，低于门槛的只计入 summary.signals_below_threshold。

编入清单 kge-compile-plan/1：jobs（与 CompileJob 字段相同，触发原子带来源类型）、signals、summary。
命令行写清单用临时文件 + os.replace（原子写），拒绝写到输入文件上或页面目录里。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from ..atoms import ATOM_TYPES, Snapshot, SnapshotError, diff_snapshots, load_snapshot
from ..lint.checks import valid_atom_id
from ..nodeid import is_node_id
from ..pages.format import Page
from ..retrieval.signals import REASONS, SIGNAL_FIELDS, SIGNAL_FORMAT, GapSignal, summarize
from .jobs import RISK_WEIGHT, CompileJob, plan_jobs

CHANGES_FORMAT = "kge-atom-changes/1"
PLAN_FORMAT = "kge-compile-plan/1"
CHANGE_KINDS = ("added", "modified", "removed")
SOURCE_ATOM_TYPES: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "exam_spec": ("region", "curriculum"), "past_paper": ("region",), "approved_upload": ("region",),
    "confirmed_pitfall": ("node", "edge"), "curriculum": ("curriculum", "node"), "textbook": ("projection",),
    "current_affair": ("affair",), "map_edit": ("node", "edge"),
})
SOURCE_TYPES = tuple(SOURCE_ATOM_TYPES)
# 兜底信号聚合后的最小门槛（规则 13：群体数据只以聚合统计回流，设最小人数门槛）。待人确认：门槛取值，以及按问法摘要计数能否代表人数
DEFAULT_SIGNAL_MIN_GROUP = 20
MAX_INPUT_BYTES = 10 * 1024 * 1024
MAX_CHANGES = 10000
_DIGEST = re.compile(r"^[0-9a-f]{16}$")
_SEMVER = r"(?:0|[1-9]\d{0,3})\.(?:0|[1-9]\d{0,3})\.(?:0|[1-9]\d{0,5})"
_PAGE_HIT = re.compile(rf"^page:([A-Z][A-Za-z0-9_.]{{0,199}})@{_SEMVER}#[a-z_]{{1,40}}$")
_ATOM_HIT = re.compile(r"^atom:([a-z]+):(.{1,200})$")


class EventError(ValueError):
    """清单或信号不合格式、与快照对不上；消息只含字段名与编号"""


@dataclass(frozen=True)
class AtomChange:
    key: str
    change: str
    source_type: str


def _read_bytes(path: str | Path, what: str) -> bytes:
    data = Path(path).read_bytes()
    if len(data) > MAX_INPUT_BYTES:
        raise EventError(f"{what}超过 10 MB")
    return data


def parse_changes(raw: Any) -> tuple[str, str, tuple[AtomChange, ...]]:
    """（起始地图版本，目标地图版本，变更）"""
    if not isinstance(raw, dict) or raw.get("format") != CHANGES_FORMAT:
        raise EventError(f"原子变更清单的 format 必须是 {CHANGES_FORMAT}")
    if set(raw) - {"format", "from_map_version", "to_map_version", "changes", "note"}:
        raise EventError("原子变更清单只收 format、from_map_version、to_map_version、changes、note")
    frm, to, changes = raw.get("from_map_version"), raw.get("to_map_version"), raw.get("changes")
    if not isinstance(frm, str) or not isinstance(to, str) or not isinstance(changes, list):
        raise EventError("原子变更清单缺 from_map_version、to_map_version 或 changes")
    if len(changes) > MAX_CHANGES:
        raise EventError(f"原子变更清单超过 {MAX_CHANGES} 条")
    out: list[AtomChange] = []
    seen: set[str] = set()
    for i, c in enumerate(changes):
        if not isinstance(c, dict) or set(c) != {"atom", "change", "source_type"}:
            raise EventError(f"changes[{i}] 只收 atom、change、source_type")
        key = c["atom"]
        atype, _, aid = key.partition(":") if isinstance(key, str) else ("", "", "")
        if atype not in ATOM_TYPES or not valid_atom_id(atype, aid):
            raise EventError(f"changes[{i}] 的 atom 不是合规范的原子键（<类型>:<ID>）")
        if c["change"] not in CHANGE_KINDS:
            raise EventError(f"changes[{i}] 的 change 只收 {'/'.join(CHANGE_KINDS)}")
        if c["source_type"] not in SOURCE_ATOM_TYPES:
            raise EventError(f"changes[{i}] 的 source_type 未登记")
        if atype not in SOURCE_ATOM_TYPES[c["source_type"]]:
            raise EventError(f"changes[{i}] 的来源类型 {c['source_type']} 不会产生 {atype} 原子")
        if key in seen:
            raise EventError(f"changes[{i}] 重复")
        seen.add(key)
        out.append(AtomChange(key, c["change"], c["source_type"]))
    return frm, to, tuple(sorted(out, key=lambda x: x.key))


def load_changes(path: str | Path) -> tuple[str, str, tuple[AtomChange, ...]]:
    return parse_changes(json.loads(_read_bytes(path, "原子变更清单").decode("utf-8")))


def restrict(old: Snapshot, new: Snapshot, changes: tuple[AtomChange, ...], frm: str, to: str, *,
             allow_partial: bool = False) -> tuple[Snapshot, int]:
    """（旧快照 + 清单里的原子取新快照的版本（下架的去掉），没列进清单的差异条数）。
    清单与两个快照的差异逐条核对，对不上即拒收；缺省不许漏列"""
    if old.subject != new.subject:
        raise EventError("两个地图快照的学科不同")
    if (frm, to) != (old.map_version, new.map_version):
        raise EventError(f"清单的地图版本（{frm} → {to}）与快照（{old.map_version} → {new.map_version}）对不上")
    diff = diff_snapshots(old, new)
    atoms = dict(old.atoms)
    for c in changes:
        if diff.get(c.key) != c.change:
            raise EventError(f"清单里的 {c.key}（{c.change}）与快照差异不符（快照：{diff.get(c.key, '无变化')}）")
        if c.change == "removed":
            atoms.pop(c.key)
        else:
            atoms[c.key] = new.atoms[c.key]
    unlisted = len(set(diff) - {c.key for c in changes})
    if unlisted and not allow_partial:
        raise EventError(f"清单漏列了 {unlisted} 条快照差异（只编部分原子要显式 --allow-partial）")
    return Snapshot(new.map_version, new.subject, new.status, new.standard_year, MappingProxyType(atoms)), unlisted


def plan_from_changes(pages: Mapping[str, Page], old: Snapshot, new: Snapshot, changes: tuple[AtomChange, ...], frm: str,
                      to: str, *, allow_partial: bool = False) -> tuple[list[CompileJob], int]:
    snap, unlisted = restrict(old, new, changes, frm, to, allow_partial=allow_partial)
    return plan_jobs(pages, old, snap), unlisted


def _valid_hit(h: Any) -> bool:
    if not isinstance(h, str) or len(h) > 300:
        return False
    m = _PAGE_HIT.match(h)
    if m:
        return is_node_id(m.group(1))
    m = _ATOM_HIT.match(h)
    return bool(m) and m.group(1) in ATOM_TYPES and valid_atom_id(m.group(1), m.group(2))


def parse_signal(line: str, n: int) -> GapSignal:
    try:
        d = json.loads(line)
    except json.JSONDecodeError:
        raise EventError(f"信号第 {n} 行不是 JSON") from None
    if not isinstance(d, dict) or set(d) != set(SIGNAL_FIELDS) or d.get("format") != SIGNAL_FORMAT:
        raise EventError(f"信号第 {n} 行的字段不在白名单或 format 不是 {SIGNAL_FORMAT}")
    nid, hits = d["node_id"], d["hits"]
    if nid is not None and not is_node_id(nid):
        raise EventError(f"信号第 {n} 行的 node_id 不合规范")
    if d["reason"] not in REASONS or not isinstance(d["query_digest"], str) or not _DIGEST.match(d["query_digest"]):
        raise EventError(f"信号第 {n} 行的 reason 或 query_digest 不合规范")
    if not isinstance(d["query_terms"], int) or isinstance(d["query_terms"], bool) or d["query_terms"] < 0:
        raise EventError(f"信号第 {n} 行的 query_terms 不合规范")
    if not isinstance(hits, list) or not all(_valid_hit(h) for h in hits):
        raise EventError(f"信号第 {n} 行的 hits 只收 page:<ID>@<版本>#<段落> 或 atom:<原子键>")
    if not isinstance(d["top_score"], (int, float)) or isinstance(d["top_score"], bool):
        raise EventError(f"信号第 {n} 行的 top_score 不合规范")
    return GapSignal(nid, d["reason"], d["query_digest"], d["query_terms"], tuple(hits), float(d["top_score"]))


def load_signals(path: str | Path) -> list[GapSignal]:
    text = _read_bytes(path, "兜底检索信号").decode("utf-8")
    return [parse_signal(ln, n) for n, ln in enumerate(text.split("\n"), 1) if ln.strip()]


def signal_entries(signals: list[GapSignal], pages: Mapping[str, Page],
                   min_group: int = DEFAULT_SIGNAL_MIN_GROUP) -> tuple[list[dict[str, Any]], int]:
    """（按（节点，原因）聚合后达到门槛的组，低于门槛的组数）。已有页面的 → review_page（补内容或全量重编，人发起），
    有节点没页面的 → new_page，没定位到节点的 → triage"""
    if not isinstance(min_group, int) or isinstance(min_group, bool) or min_group < 1:
        raise EventError("信号门槛至少为 1")
    by_node = {str(p.header.get("node_id")): pid for pid, p in pages.items()}
    out = []
    below = 0
    for row in summarize(signals):
        if row["count"] < min_group or row["distinct_queries"] < min_group:
            below += 1
            continue
        nid = row["node_id"]
        page_id = by_node.get(nid) if nid else None
        action = "review_page" if page_id else ("new_page" if nid else "triage")
        out.append({**row, "page_id": page_id, "action": action, "risk_estimate": "high"})
    return out, below


def job_dict(job: CompileJob, sources: Mapping[str, str]) -> dict[str, Any]:
    return {"job_id": job.job_id, "page_id": job.page_id, "reason": job.reason, "rebuild_kind": job.rebuild_kind,
            "risk_estimate": job.risk_estimate, "impact": job.impact, "priority": job.priority, "status": job.status,
            "triggers": [{"atom": t.key, "change": t.change, "source_type": sources.get(t.key)} for t in job.triggers]}


def build_plan(pages: Mapping[str, Page], *, old: Snapshot | None = None, new: Snapshot | None = None,
               changes: tuple[str, str, tuple[AtomChange, ...]] | None = None, signals: list[GapSignal] | None = None,
               allow_partial: bool = False, signal_min_group: int = DEFAULT_SIGNAL_MIN_GROUP) -> dict[str, Any]:
    jobs: list[CompileJob] = []
    sources: dict[str, str] = {}
    unlisted = 0
    if changes is not None:
        if old is None or new is None:
            raise EventError("给了原子变更清单就必须给新旧两个地图快照")
        frm, to, items = changes
        jobs, unlisted = plan_from_changes(pages, old, new, items, frm, to, allow_partial=allow_partial)
        sources = {c.key: c.source_type for c in items}
    sig, below = signal_entries(signals or [], pages, signal_min_group)
    return {"format": PLAN_FORMAT,
            "from_map_version": old.map_version if old and changes is not None else None,
            "to_map_version": new.map_version if new and changes is not None else None,
            "jobs": [job_dict(j, sources) for j in jobs], "signals": sig,
            "summary": {"jobs": len(jobs), "by_risk": {r: sum(1 for j in jobs if j.risk_estimate == r) for r in RISK_WEIGHT},
                        "changes": len(sources), "unlisted": unlisted, "partial": bool(allow_partial and changes is not None),
                        "signal_groups": len(sig), "signals_below_threshold": below, "signal_min_group": signal_min_group}}


def check_out_path(out: str, inputs: list[str], pages_dir: str) -> Path:
    """--out 不能是任何输入文件，也不能落在页面目录里"""
    target = Path(out).resolve()
    for p in inputs:
        if Path(p).resolve() == target:
            raise EventError("--out 不能指向输入文件")
    pages = Path(pages_dir).resolve()
    if target == pages or pages in target.parents:
        raise EventError("--out 不能写进页面目录")
    return target


def write_atomic(target: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".kge-plan-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m knowledge.compiler plan-events",
                                 description="文件式编入事件（KGE-11）：原子变更清单 / 兜底检索信号 → 编译任务清单（只产出清单）")
    ap.add_argument("--pages", required=True, help="页面目录（页面仓库主分支的 pages/）")
    ap.add_argument("--changes", help="原子变更清单（kge-atom-changes/1）")
    ap.add_argument("--old-snapshot", help="页面当前所依据的地图快照（给了 --changes 时必填）")
    ap.add_argument("--new-snapshot", help="原子变更后的地图快照（给了 --changes 时必填）")
    ap.add_argument("--allow-partial", action="store_true", help="允许清单只列部分差异（summary.unlisted 报漏列数）")
    ap.add_argument("--signals", help="兜底检索信号（kge-gap-signal/1 JSON Lines）")
    ap.add_argument("--signal-min-group", type=int, default=DEFAULT_SIGNAL_MIN_GROUP,
                    help=f"信号聚合后列出的最小次数与不同问法数（缺省 {DEFAULT_SIGNAL_MIN_GROUP}，待人确认）")
    ap.add_argument("--out", help="编入清单写到这个文件（缺省输出到标准输出）")
    args = ap.parse_args(argv)
    try:
        if not args.changes and not args.signals:
            raise EventError("至少给 --changes 或 --signals 之一")
        if args.changes and not (args.old_snapshot and args.new_snapshot):
            raise EventError("给了 --changes 就必须给 --old-snapshot 与 --new-snapshot")
        target = None
        if args.out:
            inputs = [p for p in (args.changes, args.old_snapshot, args.new_snapshot, args.signals) if p]
            target = check_out_path(args.out, inputs, args.pages)
        from .output import load_repo  # noqa: PLC0415
        repo = load_repo(args.pages)
        pages = {pid: f[1] for pid, f in repo.files.items()}
        old = load_snapshot(args.old_snapshot) if args.old_snapshot else None
        new = load_snapshot(args.new_snapshot) if args.new_snapshot else None
        changes = load_changes(args.changes) if args.changes else None
        signals = load_signals(args.signals) if args.signals else None
        plan = build_plan(pages, old=old, new=new, changes=changes, signals=signals, allow_partial=args.allow_partial,
                          signal_min_group=args.signal_min_group)
        text = json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if target is not None:
            write_atomic(target, text)
    except (EventError, SnapshotError, OSError, ValueError) as e:
        print(f"编入清单无法生成：{e}", file=sys.stderr)
        return 2
    if target is not None:
        s = plan["summary"]
        print(f"编入清单：{s['jobs']} 个编译任务（高 {s['by_risk']['high']}、中 {s['by_risk']['medium']}、低 {s['by_risk']['low']}），"
              f"信号 {s['signal_groups']} 组（样本不足 {s['signals_below_threshold']} 组），漏列差异 {s['unlisted']} 条 → {args.out}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
