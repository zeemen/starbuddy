"""编译产出（spec 设计要点 3 末条）：本环境没有页面仓库，编译器只输出到本地目录，并为每个合并请求写说明
（差异、依据原子、体检结果）。推送到页面仓库由执行人配置，凭据不在本仓；本模块不做任何网络访问。

目录结构：
  <out>/<合并请求 ID>/pages/<页面相对路径>     合并请求里的新版页面
  <out>/<合并请求 ID>/MERGE_REQUEST.md          给审核人看的说明
  <out>/<合并请求 ID>/merge_request.json        同一内容的结构化版本（含体检 JSON）
"""
from __future__ import annotations

import datetime as dt
import difflib
import json
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..atoms import Snapshot
from ..lint import lint_dir
from ..lint.issues import LintReport
from ..lint.lexicon import Lexicon
from ..pages.format import Page
from .jobs import CompileJob, plan_jobs
from .rebuild import CompileResult, render_rebuild
from .window import MergeRequest

RISK_ZH = {"low": "低", "medium": "中", "high": "高"}


@dataclass(frozen=True)
class PageRepo:
    root: Path
    files: Mapping[str, tuple[str, Page, str]]  # 页面 ID → （相对路径，页面，原文）

    @property
    def page_ids(self) -> set[str]:
        return set(self.files)


def load_repo(pages_dir: str | Path) -> PageRepo:
    from ..lint.checks import load_files
    root = Path(pages_dir)
    files = {}
    for pf in load_files(root):
        if pf.page is None:
            raise ValueError(f"页面无法解析，先修好再编译：{pf.rel}")
        files[pf.page.page_id] = (pf.rel, pf.page, pf.text)
    return PageRepo(root, files)


def compile_render(repo: PageRepo, old: Snapshot, new: Snapshot, compiled_at: dt.datetime) -> tuple[list[CompileJob], list[CompileResult]]:
    """编入 → 渲染重建（按优先级顺序）"""
    jobs = plan_jobs({pid: f[1] for pid, f in repo.files.items()}, old, new)
    results = []
    for job in jobs:
        rel, page, text = repo.files[job.page_id]
        results.append(render_rebuild(job, rel, page, text, new, repo.page_ids, compiled_at))
    return jobs, results


def unified_diff(r: CompileResult) -> str:
    return "\n".join(difflib.unified_diff(r.old_text.split("\n"), r.new_text.split("\n"), f"a/{r.rel_path}", f"b/{r.rel_path}",
                                          lineterm="", n=2))


def lint_merge_request(repo: PageRepo, mr: MergeRequest, snapshot: Snapshot, lexicon: Lexicon | None, today: dt.date) -> LintReport:
    """把合并请求叠到页面仓库当前内容上，整库体检（孤立页、失效链接要看全库），上一审核版 = 当前内容"""
    with tempfile.TemporaryDirectory(prefix="kge-mr-") as tmp:
        work = Path(tmp) / "pages"
        shutil.copytree(repo.root, work)
        for r in mr.results:
            (work / r.rel_path).write_text(r.new_text, encoding="utf-8")
        return lint_dir(work, snapshot=snapshot, baseline_dir=repo.root, lexicon=lexicon, today=today)


def describe(mr: MergeRequest, report: LintReport, snapshot: Snapshot) -> tuple[str, dict[str, Any]]:
    ids = set(mr.page_ids)
    issues = [i for i in report.issues if i.page_id in ids or i.page_id == "*"]
    blocks = [i for i in issues if i.severity == "block"]
    title = (f"每日低风险合并窗口 {mr.day.isoformat()}（{len(mr.results)} 页）" if mr.kind == "daily_low"
             else f"{mr.results[0].page_id} {RISK_ZH[mr.risk]}风险编译")
    md = [f"# [KGE 编译] {title}", "",
          f"- 合并请求：`{mr.mr_id}`",
          f"- 类型：{'每日低风险合并窗口' if mr.kind == 'daily_low' else '单独请求'}；风险：{RISK_ZH[mr.risk]}；"
          f"审核人数：{mr.reviewers_required if mr.reviewers_required else '不需要（体检通过即可合并，事后抽检）'}",
          f"- 日期（北京时间）：{mr.day.isoformat()}；地图版本：{snapshot.subject} {snapshot.map_version}",
          f"- 程序体检：阻断 {len(blocks)}，警告 {len(issues) - len(blocks)}" + ("（有阻断，不能合并）" if blocks else ""),
          *[f"- {n}" for n in mr.notes], ""]
    pages_json = []
    for r in mr.results:
        md += [f"## {r.page_id}（{r.old_page.page_version} → {r.new_page.page_version}，"
               f"{'渲染重建' if r.rebuild_kind == 'render' else '全量重编'}）", "",
               "改动原因：" + "；".join(r.reasons), "",
               "变化段落：" + ("、".join(r.changed_sections) or "无（只有头部）"), "",
               f"编译任务：`{r.job.job_id}`（{r.job.reason}，优先级 {r.job.priority} = 风险 × 影响 {r.job.impact}）", "",
               "### 依据原子", "", "| 原子 | 变化 | 旧哈希 | 新哈希 |", "|---|---|---|---|"]
        md += [f"| `{b.key}` | {b.change} | `{b.old_hash or '—'}` | `{b.new_hash or '—'}` |" for b in r.basis] or ["| — | — | — | — |"]
        page_issues = [i for i in issues if i.page_id == r.page_id]
        md += ["", "### 差异", "", "```diff", unified_diff(r), "```", "", "### 体检结果", ""]
        md += [f"- {'阻断' if i.severity == 'block' else '警告'} `{i.check_type}` 第 {i.line or '-'} 行：{i.message}" for i in page_issues] or ["- 通过"]
        md.append("")
        pages_json.append({"page_id": r.page_id, "file": r.rel_path, "old_version": r.old_page.page_version,
                           "new_version": r.new_page.page_version, "rebuild_kind": r.rebuild_kind, "risk": r.risk,
                           "reasons": list(r.reasons), "changed_sections": list(r.changed_sections),
                           "job": {"job_id": r.job.job_id, "reason": r.job.reason, "priority": r.job.priority, "impact": r.job.impact,
                                   "triggers": [{"atom": t.key, "change": t.change} for t in r.job.triggers]},
                           "basis_atoms": [{"atom": b.key, "change": b.change, "old_hash": b.old_hash, "new_hash": b.new_hash} for b in r.basis],
                           "source_version": r.source_version, "diff": unified_diff(r)})
    data = {"format": "kge-merge-request/1", "mr_id": mr.mr_id, "kind": mr.kind, "day": mr.day.isoformat(), "risk": mr.risk,
            "reviewers_required": mr.reviewers_required, "map_version": snapshot.map_version, "subject": snapshot.subject,
            "notes": list(mr.notes), "pages": pages_json,
            "lint": {"blocks": len(blocks), "warnings": len(issues) - len(blocks), "issues": [i.to_dict() for i in issues]}}
    return "\n".join(md), data


def write_merge_requests(repo: PageRepo, mrs: list[MergeRequest], snapshot: Snapshot, out_dir: str | Path,
                         lexicon: Lexicon | None = None, today: dt.date | None = None) -> list[dict[str, Any]]:
    out = Path(out_dir)
    if out.resolve() == repo.root.resolve() or repo.root.resolve() in out.resolve().parents:
        raise ValueError("输出目录不能在页面目录里")
    if out.exists() and any(out.iterdir()):
        raise ValueError("输出目录已存在且不是空目录")
    summaries = []
    for mr in mrs:
        report = lint_merge_request(repo, mr, snapshot, lexicon, today or mr.day)
        md, data = describe(mr, report, snapshot)
        d = out / mr.mr_id
        for r in mr.results:
            target = d / "pages" / r.rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(r.new_text, encoding="utf-8")
        (d / "MERGE_REQUEST.md").write_text(md, encoding="utf-8")
        (d / "merge_request.json").write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        summaries.append({"mr_id": mr.mr_id, "risk": mr.risk, "pages": mr.page_ids, "reviewers_required": mr.reviewers_required,
                          "lint_blocks": data["lint"]["blocks"]})
    return summaries
