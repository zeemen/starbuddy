"""规则式语义体检（KGE-11 第一步；KGE V1.1“体检两层”的语义层）。确定性、不调模型、不连库。

程序体检（knowledge.lint，KGE-03）先跑，语义体检随后；报告是 kge-lint-report/1.1，顶层 layer = semantic，条目 layer = semantic，
检查类型并入 knowledge.lint.issues 的枚举。比对范围只到骨架相邻页（前置、易混、同主题）与本次受影响页，不做全量两两。
说明见同目录 README.md。

命令行：python -m knowledge.semantic <页面目录> --map-snapshot 快照 [--affected 编入清单 | --affected-pages ID,...]
        [--scope-vocab 超纲词表] [--style-lexicon 文风词表] [--terms 术语表] [--levels 分级配置] [--json]
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from ..atoms import Snapshot
from ..lint.binding import scope_pages, sha256_text, snapshot_digest
from ..lint.checks import PageFile, load_files
from ..lint.issues import LintIssue, LintReport
from ..lint.lexicon import Lexicon
from .checks import SemanticContext, global_issue, run_page_checks, run_pair_checks
from .config import DEFAULT_LEVELS, levels_config
from .inputs import EMPTY_TERMS, ScopeVocab, StyleLexicon, TermTable

__all__ = ["SemanticContext", "SemanticResult", "comparison_scope", "lint_semantic", "lint_semantic_dir", "skeleton_neighbors", "topic_of"]

NEIGHBOR_EDGE_TYPES = ("prerequisite", "confusable")
TOPIC_SEGMENTS = 3  # 同主题 = 节点 ID 前三段相同（学科.领域.单元，见 docs/design/kge 节点 ID 规范）


@dataclass(frozen=True)
class SemanticResult:
    report: LintReport
    scope: tuple[str, ...]  # 本次检查的页面
    pairs: tuple[tuple[str, str], ...]  # 本次比对的页面对（受影响页 × 它的骨架相邻页）
    missing: tuple[str, ...] = ()  # 受影响页里不在页面目录的（报 affected_scope 阻断）


def topic_of(node_id: str) -> str | None:
    """节点所在主题：ID 前三段（至少四段的节点才有；三段及以下本身就是领域或主题）"""
    parts = node_id.split(".")
    return ".".join(parts[:TOPIC_SEGMENTS]) if len(parts) > TOPIC_SEGMENTS else None


def skeleton_neighbors(snap: Snapshot, node_id: str) -> set[str]:
    """骨架相邻：前置（双向）、易混（双向）、同主题（节点 ID 前三段相同的有效节点）"""
    out: set[str] = set()
    for t in NEIGHBOR_EDGE_TYPES:
        out |= {e.record["from_id"] for e in snap.edges_to(node_id, t)} | {e.record["to_id"] for e in snap.edges_from(node_id, t)}
    topic = topic_of(node_id)
    if topic is not None:
        out |= {a.id for a in snap.of_type("node") if topic_of(a.id) == topic and a.record.get("status", "active") == "active"}
    out.discard(node_id)
    return out


def comparison_scope(pages: Mapping[str, PageFile], snap: Snapshot,
                     affected: Iterable[str] | None) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    """（检查的页面，比对的页面对）。affected 为 None 时每页都算受影响（每日全量），页面对仍只到骨架相邻。
    页面 ID 即节点 ID（KGE 第二章）；不在页面目录里的受影响页不进范围（由 lint_semantic 报 affected_scope 阻断）"""
    by_node = {str(pf.page.header.get("node_id")): pid for pid, pf in pages.items() if pf.page is not None}
    hit = set(pages) if affected is None else {p for p in affected if p in pages}
    scope = set(hit)
    pairs: set[tuple[str, str]] = set()
    for pid in hit:
        node_id = str(pages[pid].page.header.get("node_id"))  # type: ignore[union-attr]
        for n in skeleton_neighbors(snap, node_id):
            other = by_node.get(n)
            if other is not None and other != pid:
                scope.add(other)
                pairs.add(tuple(sorted((pid, other))))  # type: ignore[arg-type]
    return tuple(sorted(scope)), tuple(sorted(pairs))


def lint_semantic(ctx: SemanticContext) -> SemanticResult:
    scope, pairs = comparison_scope(ctx.pages, ctx.snapshot, ctx.affected)
    missing = tuple(sorted(p for p in ctx.affected or () if p not in ctx.pages))
    issues: list[LintIssue] = []
    skipped: list[str] = []
    for pid in missing:
        issues.append(global_issue(ctx, "affected_scope", "missing", f"受影响页 {pid} 不在页面目录里（编入清单与页面目录对不上）",
                                   page_id=pid))
    if ctx.scope_vocab is None:
        skipped.append("out_of_scope")
        issues.append(global_issue(ctx, "out_of_scope", "no_vocab", "没有指定超纲词表（--scope-vocab）：超纲检查跳过"))
    elif ctx.scope_vocab.synthetic:
        issues.append(global_issue(ctx, "out_of_scope", "synthetic_vocab", "超纲词表是合成样例：超纲结论只演示规则，不代表课标范围"))
    if ctx.style is None:
        skipped.append("age_style")
        issues.append(global_issue(ctx, "age_style", "no_lexicon", "没有指定适龄文风词表（--style-lexicon）：文风词表规则跳过"))
    for pid in scope:
        issues += run_page_checks(ctx, ctx.pages[pid])
    issues += run_pair_checks(ctx, pairs)
    unique: dict[str, LintIssue] = {}
    for i in issues:  # 同一条（同页同行同结论）只留一次
        unique.setdefault(f"{i.to_dict()['id']}|{i.line}", i)
    issues = sorted(unique.values(), key=lambda i: (i.file, i.line or 0, i.check_type, i.severity, i.message))

    def synthetic(x: object) -> bool | None:
        return None if x is None else bool(getattr(x, "synthetic", True))

    terms_given = ctx.terms is not EMPTY_TERMS
    report = LintReport(
        tuple(issues), len(scope), "semantic", ctx.snapshot.map_version,
        {"pages": scope_pages(ctx.files, scope), "affected": None if ctx.affected is None else sorted(ctx.affected),
         "missing": list(missing), "pairs": [list(p) for p in pairs]},
        {"map_snapshot": snapshot_digest(ctx.snapshot), "lexicon": sha256_text(ctx.lexicon.to_json()),
         "scope_vocab": ctx.scope_vocab.digest if ctx.scope_vocab else None,
         "style_lexicon": ctx.style.digest if ctx.style else None,
         "terms": ctx.terms.digest if terms_given else None},
        {**levels_config(ctx.levels),
         "synthetic_inputs": {"scope_vocab": synthetic(ctx.scope_vocab), "style_lexicon": synthetic(ctx.style),
                              "terms": synthetic(ctx.terms) if terms_given else None},
         "skipped_checks": sorted(skipped)})
    return SemanticResult(report, scope, pairs, missing)


def lint_semantic_dir(pages_dir: str | Path, *, snapshot: Snapshot, lexicon: Lexicon, scope_vocab: ScopeVocab | None = None,
                      style: StyleLexicon | None = None, terms: TermTable = EMPTY_TERMS, levels: Mapping[str, str] = DEFAULT_LEVELS,
                      affected: Iterable[str] | None = None) -> SemanticResult:
    files = load_files(Path(pages_dir))
    ctx = SemanticContext(files, snapshot, lexicon, scope_vocab, style, terms, levels,
                          None if affected is None else frozenset(affected))
    return lint_semantic(ctx)
