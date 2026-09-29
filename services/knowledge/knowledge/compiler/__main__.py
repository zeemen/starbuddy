"""python -m knowledge.compiler --pages 页面目录 --old-snapshot 旧快照 --new-snapshot 新快照 --out 输出目录

编入 → 渲染重建 → 合并窗口 → 每个合并请求叠加体检 → 写到本地输出目录。不推送任何远程仓库。
全量重编要经模型路由，只提供 Python 接口（knowledge.compiler.full.full_recompile），不在命令行里调用模型。
退出码：0 完成；1 有合并请求体检阻断；2 参数或输入错误。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from ..atoms import SnapshotError, load_snapshot
from ..lint.lexicon import Lexicon, LexiconError
from .output import compile_render, load_repo, write_merge_requests
from .rebuild import CST
from .window import plan_window


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m knowledge.compiler", description="编译层编译器（KGE-03）：渲染重建与合并窗口")
    ap.add_argument("--pages", required=True, help="页面目录（页面仓库主分支的 pages/）")
    ap.add_argument("--old-snapshot", required=True, help="页面当前所依据的地图快照")
    ap.add_argument("--new-snapshot", required=True, help="原子变更后的地图快照")
    ap.add_argument("--out", required=True, help="输出目录（必须是新目录，不能在页面目录里）")
    ap.add_argument("--now", help="编译时刻（ISO 8601，默认现在）；决定合并窗口的日期（北京时间）")
    ap.add_argument("--published-log", help="JSON：{页面 ID: [已发布低风险改动的日期]}")
    ap.add_argument("--lexicon", help="人设词表（默认从本仓 docs/personas 只读抽取）")
    args = ap.parse_args(argv)
    try:
        repo = load_repo(args.pages)
        old, new = load_snapshot(args.old_snapshot), load_snapshot(args.new_snapshot)
        now = dt.datetime.fromisoformat(args.now) if args.now else dt.datetime.now(CST)
        if now.tzinfo is None:
            now = now.replace(tzinfo=CST)
        published = {}
        if args.published_log:
            raw = json.loads(Path(args.published_log).read_text(encoding="utf-8"))
            published = {pid: [dt.date.fromisoformat(d) for d in days] for pid, days in raw.items()}
        lexicon = Lexicon.from_json(Path(args.lexicon).read_text(encoding="utf-8")) if args.lexicon else None
        jobs, results = compile_render(repo, old, new, now)
        mrs = plan_window(results, published)
        summaries = write_merge_requests(repo, mrs, new, args.out, lexicon)
    except (SnapshotError, LexiconError, ValueError, OSError) as e:
        print(f"编译无法运行：{e}", file=sys.stderr)
        return 2
    print(f"编入：{len(jobs)} 个编译任务；有变化 {sum(not r.unchanged for r in results)} 页；合并请求 {len(mrs)} 个")
    for s in summaries:
        print(f"  {s['mr_id']}：风险 {s['risk']}，{len(s['pages'])} 页，审核人数 {s['reviewers_required']}，体检阻断 {s['lint_blocks']}")
    return 1 if any(s["lint_blocks"] for s in summaries) else 0


if __name__ == "__main__":
    sys.exit(main())
