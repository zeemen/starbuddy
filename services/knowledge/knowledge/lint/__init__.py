"""程序体检（spec 设计要点 4）：每次编译后 + 每日全量；有阻断即不能合并。

命令行：python -m knowledge.lint <页面目录> [--map-snapshot 快照] [--baseline 上一审核版目录] [--json]
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from ..atoms import Snapshot
from . import checks
from .checks import DEFAULT_MIN_GROUP, LintContext, PageFile, load_files
from .issues import LintIssue, LintReport
from .lexicon import Lexicon, default_lexicon

__all__ = ["DEFAULT_MIN_GROUP", "LintContext", "LintIssue", "LintReport", "PageFile", "lint_context", "lint_dir", "load_files"]


def lint_context(ctx: LintContext) -> LintReport:
    issues: list[LintIssue] = []
    if ctx.snapshot is None:
        issues.append(LintIssue("snapshot", "warn", "*", "", "", "没有指定地图快照（--map-snapshot）：节点存在、模板、原子哈希、过期、"
                                "教材原句检查都跳过；页面仓库 CI 必须指定"))
    elif ctx.snapshot.status != "published":
        issues.append(LintIssue("snapshot", "warn", "*", "", "", f"地图快照 {ctx.snapshot.map_version} 的状态是 {ctx.snapshot.status}，"
                                "不是已发布版本"))
    for pf in ctx.files:
        issues += checks.check_format(pf)
        if pf.page is None:
            continue
        issues += checks.check_header(pf)
        issues += checks.check_node(pf, ctx)
        issues += checks.check_sections(pf)
        issues += checks.check_template(pf, ctx)
        issues += checks.check_locked_baseline(pf, ctx)
        issues += checks.check_links(pf, ctx)
        issues += checks.check_citations(pf)
        issues += checks.check_atom_hash(pf, ctx)
        issues += checks.check_expiry(pf, ctx)
        issues += checks.check_formulas(pf)
        issues += checks.check_pii(pf)
        issues += checks.check_group_stats(pf, ctx)
        issues += checks.check_persona(pf, ctx.lexicon)
        issues += checks.check_sentence_length(pf, ctx.lexicon)
        issues += checks.check_textbook_quote(pf, ctx)
    issues += checks.check_orphans(ctx)
    issues.sort(key=lambda i: (i.file, i.line or 0, i.check_type, i.severity, i.message))
    return LintReport(tuple(issues), sum(1 for pf in ctx.files))


def lint_dir(pages_dir: str | Path, *, snapshot: Snapshot | None = None, baseline_dir: str | Path | None = None,
             lexicon: Lexicon | None = None, today: dt.date | None = None, min_group: int = DEFAULT_MIN_GROUP) -> LintReport:
    files = load_files(Path(pages_dir))
    baseline = {}
    if baseline_dir is not None:
        baseline = {pf.page.page_id: pf.page for pf in load_files(Path(baseline_dir)) if pf.page is not None}
    ctx = LintContext(files, lexicon or default_lexicon(), snapshot, baseline, today or dt.date.today(), min_group)
    return lint_context(ctx)
