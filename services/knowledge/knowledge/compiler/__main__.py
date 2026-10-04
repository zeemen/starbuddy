"""python -m knowledge.compiler --pages 页面目录 --old-snapshot 旧快照 --new-snapshot 新快照 --out 输出目录
    (--hold-report 页面对账报告 [--hold-max-age-hours 24] | --no-hold-report)

对账报告必填（开发时显式 --no-hold-report）：报告过期（exported_at 早于 --now 减有效期）、格式不对或自相矛盾都以退出码 2 拒绝。

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
from .window import DEFAULT_HOLD_MAX_AGE, plan_window, read_hold_report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m knowledge.compiler", description="编译层编译器（KGE-03）：渲染重建与合并窗口")
    ap.add_argument("--pages", required=True, help="页面目录（页面仓库主分支的 pages/）")
    ap.add_argument("--old-snapshot", required=True, help="页面当前所依据的地图快照")
    ap.add_argument("--new-snapshot", required=True, help="原子变更后的地图快照")
    ap.add_argument("--out", required=True, help="输出目录（必须是新目录，不能在页面目录里）")
    ap.add_argument("--now", help="编译时刻（ISO 8601，默认现在）；决定合并窗口的日期（北京时间）")
    ap.add_argument("--published-log", help="JSON：{页面 ID: [已发布低风险改动的日期]}")
    ap.add_argument("--lexicon", help="人设词表（默认从本仓 docs/personas 只读抽取）")
    hold = ap.add_mutually_exclusive_group(required=True)
    hold.add_argument("--hold-report", help="页面对账报告（kge-pages-reconcile/1，审核台 reconcile-pages 产出）：held_pages 里的页面本窗口跳过")
    hold.add_argument("--no-hold-report", action="store_true", help="不读对账报告（只用于开发与测试；正式的合并窗口必须给报告）")
    ap.add_argument("--hold-max-age-hours", type=float, default=DEFAULT_HOLD_MAX_AGE.total_seconds() / 3600,
                    help="对账报告的有效期（小时，缺省 24 = 一个合并窗口周期）；按 --now 计")
    args = ap.parse_args(argv)
    try:
        now = dt.datetime.fromisoformat(args.now) if args.now else dt.datetime.now(CST)
        if now.tzinfo is None:
            now = now.replace(tzinfo=CST)
        if not args.hold_max_age_hours > 0:
            raise ValueError("--hold-max-age-hours 必须大于 0")
        held: frozenset[str] = frozenset()
        if args.hold_report:
            held = read_hold_report(args.hold_report, now=now, max_age=dt.timedelta(hours=args.hold_max_age_hours))
        repo = load_repo(args.pages)
        old, new = load_snapshot(args.old_snapshot), load_snapshot(args.new_snapshot)
        published = {}
        if args.published_log:
            raw = json.loads(Path(args.published_log).read_text(encoding="utf-8"))
            published = {pid: [dt.date.fromisoformat(d) for d in days] for pid, days in raw.items()}
        lexicon = Lexicon.from_json(Path(args.lexicon).read_text(encoding="utf-8")) if args.lexicon else None
        jobs, results = compile_render(repo, old, new, now)
        mrs = plan_window(results, published, held)
        summaries = write_merge_requests(repo, mrs, new, args.out, lexicon)
    except (SnapshotError, LexiconError, ValueError, OSError) as e:
        print(f"编译无法运行：{e}", file=sys.stderr)
        return 2
    print(f"编入：{len(jobs)} 个编译任务；有变化 {sum(not r.unchanged for r in results)} 页；合并请求 {len(mrs)} 个")
    skipped = sorted({r.page_id for r in results if not r.unchanged and r.page_id in held})
    if held:
        print(f"  页面对账失败，本窗口跳过 {len(skipped)} 页（对账报告共列 {len(held)} 页）：{'、'.join(skipped) or '无'}")
    for s in summaries:
        print(f"  {s['mr_id']}：风险 {s['risk']}，{len(s['pages'])} 页，审核人数 {s['reviewers_required']}，体检阻断 {s['lint_blocks']}")
    return 1 if any(s["lint_blocks"] for s in summaries) else 0


if __name__ == "__main__":
    sys.exit(main())
