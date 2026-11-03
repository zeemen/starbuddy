"""python -m knowledge.semantic <页面目录> --map-snapshot 快照 [--affected 编入清单 | --affected-pages ID,...]
    [--scope-vocab 超纲词表] [--style-lexicon 文风词表] [--terms 术语表] [--levels 分级配置] [--lexicon 人设词表] [--json]

规则式语义体检（KGE-11）。先跑程序体检（python -m knowledge.lint），再跑本命令。
退出码：0 没有阻断；1 有阻断；2 参数或输入错误（快照、词表、清单读不了——fail closed，不当作通过）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..atoms import SnapshotError, load_snapshot
from ..compiler.events import PLAN_FORMAT
from ..lint.lexicon import Lexicon, LexiconError, default_lexicon
from ..nodeid import is_node_id
from . import lint_semantic_dir
from .config import LevelsError, load_levels
from .inputs import EMPTY_TERMS, InputError, load_scope, load_style, load_terms


def affected_from_plan(path: str, map_version: str) -> list[str]:
    """编入清单里的受影响页。清单的目标地图版本必须等于 --map-snapshot 的版本（清单按别的版本编出来的不能拿来定范围）"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("format") != PLAN_FORMAT or not isinstance(raw.get("jobs"), list):
        raise ValueError(f"--affected 必须是编入清单（{PLAN_FORMAT}）")
    ids = [j.get("page_id") for j in raw["jobs"] if isinstance(j, dict)]
    if len(ids) != len(raw["jobs"]) or any(not is_node_id(p) for p in ids):
        raise ValueError("编入清单里的 page_id 不合规范")
    to = raw.get("to_map_version")
    if (to is None and ids) or (to is not None and to != map_version):
        raise ValueError(f"编入清单的目标地图版本（{to}）与 --map-snapshot（{map_version}）不一致")
    return ids


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m knowledge.semantic", description="编译层页面的规则式语义体检（KGE-11）")
    ap.add_argument("pages", help="页面目录")
    ap.add_argument("--map-snapshot", required=True, help="已发布地图版本的快照（语义体检必须有原子）")
    aff = ap.add_mutually_exclusive_group()
    aff.add_argument("--affected", help="编入清单（python -m knowledge.compiler plan-events 的输出）：其中的页面是本次受影响页")
    aff.add_argument("--affected-pages", help="本次受影响页，逗号分隔的页面 ID；不给即每日全量（每页都算受影响，比对仍只到骨架相邻页）")
    ap.add_argument("--scope-vocab", help="超纲词表（kge-semantic-scope/1）；不给则超纲检查跳过并报提示")
    ap.add_argument("--style-lexicon", help="适龄文风词表（kge-semantic-style/1）；不给则文风词表规则跳过并报提示")
    ap.add_argument("--terms", help="术语表（kge-semantic-terms/1：节点别名、版次专属表述）")
    ap.add_argument("--levels", help="阻断 / 提示分级覆盖（kge-semantic-levels/1；缺省分级待教研确认）")
    ap.add_argument("--lexicon", help="人设词表（knowledge.lint --export-lexicon 导出）；在本仓运行时默认复用人设治理组件")
    ap.add_argument("--json", action="store_true", help="输出 kge-lint-report/1 JSON")
    args = ap.parse_args(argv)
    try:
        if not Path(args.pages).is_dir():
            raise ValueError("需要一个存在的页面目录")
        snapshot = load_snapshot(args.map_snapshot)
        lexicon = Lexicon.from_json(Path(args.lexicon).read_text(encoding="utf-8")) if args.lexicon else default_lexicon()
        scope = load_scope(args.scope_vocab) if args.scope_vocab else None
        style = load_style(args.style_lexicon) if args.style_lexicon else None
        terms = load_terms(args.terms) if args.terms else EMPTY_TERMS
        levels = load_levels(args.levels, style.ids if style else frozenset())
        affected: list[str] | None = None
        if args.affected:
            affected = affected_from_plan(args.affected, snapshot.map_version)
        elif args.affected_pages is not None:
            affected = [p for p in args.affected_pages.split(",") if p]
            if any(not is_node_id(p) for p in affected):
                raise ValueError("--affected-pages 里有不合规范的页面 ID")
    except (SnapshotError, LexiconError, InputError, LevelsError, OSError, ValueError) as e:
        print(f"语义体检无法运行：{e}", file=sys.stderr)
        return 2
    res = lint_semantic_dir(args.pages, snapshot=snapshot, lexicon=lexicon, scope_vocab=scope, style=style, terms=terms, levels=levels,
                            affected=affected)
    report = res.report
    if args.json:
        sys.stdout.write(report.to_json())
    else:
        for i in report.issues:
            loc = f"{i.file}:{i.line}" if i.line else (i.file or "*")
            print(f"{'阻断' if i.severity == 'block' else '提示'} [{i.check_type}] {loc} {i.message}")
        s = report.to_dict()["summary"]
        print(f"语义体检：{s['pages']} 页（比对 {len(res.pairs)} 对相邻页），阻断 {s['blocks']}，提示 {s['warnings']}")
    return 1 if report.blocked else 0


if __name__ == "__main__":
    sys.exit(main())
