"""命令行：
  python -m e2e_cohort cohort                  打印合成队列（JSON，确定性）
  python -m e2e_cohort evidence --out DIR [--starmap]  一次性 Postgres 上跑完整合成联调并生成 Gate 0 证据包（make gate0-evidence）
  python -m e2e_cohort verify --dsn-env VAR --students FILE --authorization-ref REF --expect-db NAME --out DIR
                                               真实内测：只读连接跑同一套断言，只输出汇总（RUNBOOK.md；本仓 CI 不运行）
用 services/learning 的 venv 运行（需要 asyncpg、httpx、fastapi、uvicorn 与 learning 包）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for p in (ROOT / "services/learning", ROOT / "services/learning/tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _cohort() -> int:
    from .cohort import generate
    from .replay import load_zhixing_items
    print(generate({k: v["points"] for k, v in load_zhixing_items().items()}).to_json())
    return 0


async def _evidence(out: Path, starmap: bool, starmap_students: int = 3) -> int:
    from pgharness import EphemeralPG

    from .evidence import render, write_pack
    from .runner import run_synthetic
    from .stack import missing_prerequisites
    from .starmap import capture

    why = missing_prerequisites()
    if why:
        print(f"gate0-evidence: 无法运行（{why}）", file=sys.stderr)
        return 2
    pg = EphemeralPG()
    record = None
    try:
        pg.start()
        report = await run_synthetic(pg)
        if starmap:
            # 同一一次性库、联调与断言都已完成：起 BFF 与前端，经真实会话截星图（starmap.py）
            record = capture(pg, report, render(report)["nodes.csv"], out, starmap_students)
    finally:
        pg.stop()
    write_pack(report, out, starmap_record=record)
    for c in report.checks:
        print(f"  {'通过' if c.passed else '失败'}  {c.name}")
    if record is not None:
        shots = record.get("shots", [])
        print(f"  {'通过' if record['status'] == 'captured' else '失败'}  starmap（{len(shots)} 张；"
              f"{'对账一致' if record['status'] == 'captured' else record.get('reason', '')}）")
    print(f"gate0-evidence: 已写入 {out}（合成联调，不是内测结论）")
    rc = 0
    if not report.passed:
        for c in report.failed():
            print(json.dumps(c.as_dict(), ensure_ascii=False)[:2000], file=sys.stderr)
        rc = 1
    if record is not None and record["status"] != "captured":
        problems = record.get("reconciliation", {}).get("problems", [])
        print(f"gate0-evidence: 星图截图失败：{record.get('reason')}" + ("\n  " + "\n  ".join(problems[:20]) if problems else ""),
              file=sys.stderr)
        rc = 1
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="e2e_cohort")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("cohort")
    ev = sub.add_parser("evidence")
    ev.add_argument("--out", required=True, type=Path)
    ev.add_argument("--starmap", action="store_true", help="星图截图（失败即 starmap.status = failed 并非零退出）")
    ev.add_argument("--starmap-students", type=int, default=3, help="学生视角截图的学生数（1–5，按作答模式顺序选取）")
    vf = sub.add_parser("verify")
    vf.add_argument("--dsn-env", required=True)
    vf.add_argument("--students", required=True, type=Path)
    vf.add_argument("--authorization-ref", required=True, help="同意与用途授权记录的编号（只记编号）")
    vf.add_argument("--out", required=True, type=Path, help="仓库之外的受控目录")
    vf.add_argument("--expect-db", required=True, help="试点库的库名；与连接上的 current_database() 不一致即拒绝")
    a = ap.parse_args(argv)
    if a.cmd == "cohort":
        return _cohort()
    if a.cmd == "evidence":
        return asyncio.run(_evidence(a.out, a.starmap, a.starmap_students))
    from .verify import main as verify_main
    return asyncio.run(verify_main(a.dsn_env, a.students, a.authorization_ref, a.out, a.expect_db))


if __name__ == "__main__":
    sys.exit(main())
