"""python -m knowledge.lint <页面目录> [--map-snapshot 快照] [--require-map-snapshot] [--baseline 上一审核版目录] [--json]

退出码：0 没有阻断；1 有阻断；2 参数或配置错误（快照、词表读不了——fail closed，不当作通过）。
页面仓库 CI 与快照导出必须带地图快照：CI 传 --require-map-snapshot，没给或读不了即退出码 2。
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from ..atoms import SnapshotError, load_snapshot
from . import DEFAULT_MIN_GROUP, lint_dir
from .lexicon import Lexicon, LexiconError, default_lexicon


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m knowledge.lint", description="编译层页面的程序体检（KGE-03）")
    ap.add_argument("pages", nargs="?", help="页面目录（如页面仓库的 pages/）")
    ap.add_argument("--map-snapshot", help="已发布地图版本的快照（JSON / YAML）")
    ap.add_argument("--require-map-snapshot", action="store_true",
                    help="必须给地图快照（页面仓库 CI 用）：没给即退出码 2，不降级为跳过相关检查")
    ap.add_argument("--baseline", help="上一审核版的页面目录（锁定段比对）")
    ap.add_argument("--lexicon", help="人设词表（在 StarBuddy 仓库用 --export-lexicon 导出）；在本仓运行时默认从 docs/personas 只读抽取")
    ap.add_argument("--today", help="判断过期用的日期 YYYY-MM-DD（默认今天）")
    ap.add_argument("--min-group", type=int, default=DEFAULT_MIN_GROUP, help="群体统计最小人数（默认 30）")
    ap.add_argument("--json", action="store_true", help="输出结构化 JSON（字段对应 lint_issue）")
    ap.add_argument("--export-lexicon", metavar="FILE", help="只导出人设词表到 FILE，不做体检")
    args = ap.parse_args(argv)
    try:
        lexicon = Lexicon.from_json(Path(args.lexicon).read_text(encoding="utf-8")) if args.lexicon else default_lexicon()
        if args.export_lexicon:
            Path(args.export_lexicon).write_text(lexicon.to_json(), encoding="utf-8")
            print(f"已导出人设词表：{args.export_lexicon}")
            return 0
        if not args.pages or not Path(args.pages).is_dir():
            print("需要一个存在的页面目录", file=sys.stderr)
            return 2
        if args.require_map_snapshot and not args.map_snapshot:
            print("体检无法运行：必须用 --map-snapshot 指定地图快照", file=sys.stderr)
            return 2
        snapshot = load_snapshot(args.map_snapshot) if args.map_snapshot else None
        if args.baseline and not Path(args.baseline).is_dir():
            print("--baseline 不是目录", file=sys.stderr)
            return 2
        today = dt.date.fromisoformat(args.today) if args.today else None
        if args.min_group < 1:
            print("--min-group 至少为 1", file=sys.stderr)
            return 2
    except (LexiconError, SnapshotError, OSError, ValueError) as e:
        print(f"体检无法运行：{e}", file=sys.stderr)
        return 2
    report = lint_dir(args.pages, snapshot=snapshot, baseline_dir=args.baseline, lexicon=lexicon, today=today,
                      min_group=args.min_group)
    if args.json:
        sys.stdout.write(report.to_json())
    else:
        for i in report.issues:
            loc = f"{i.file}:{i.line}" if i.line else (i.file or "*")
            print(f"{'阻断' if i.severity == 'block' else '警告'} [{i.check_type}] {loc} {i.message}")
        s = report.to_dict()["summary"]
        print(f"体检：{s['pages']} 页，阻断 {s['blocks']}，警告 {s['warnings']}")
    return 1 if report.blocked else 0


if __name__ == "__main__":
    sys.exit(main())
