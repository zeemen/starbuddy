"""命令行：导出版本记录（spec 设计要点 11，Gate 0 证据“版本记录”）。

  python -m review_console record --subject aya [--format csv|json]

只用 kge 连接（REVIEW_CONSOLE_KGE_DATABASE_URL），只读 kge.version_record；输出到标准输出。
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import sys

import asyncpg

from .app import SUBJECTS
from .config import ReviewSettings
from .diff import canonical_json
from .snapshot import norm
from .store import KgeStore

COLUMNS = ("map_version_id", "version", "action", "decision", "risk_level", "basis_code", "digest", "decided_at", "signer")


def write_records(rows: list[dict], fmt: str, out=sys.stdout) -> None:
    rows = [{c: norm(r[c]) for c in COLUMNS} for r in rows]
    if fmt == "json":
        out.write(canonical_json(rows) + "\n")
        return
    w = csv.DictWriter(out, fieldnames=COLUMNS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)


async def _record(subject: str, fmt: str) -> None:
    s = ReviewSettings.from_env()
    conn = await asyncpg.connect(s.kge_database_url)
    try:
        write_records(await KgeStore(conn).records(subject), fmt)  # type: ignore[arg-type]
    finally:
        await conn.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="review_console")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record", help="导出版本记录")
    r.add_argument("--subject", required=True, choices=SUBJECTS)
    r.add_argument("--format", default="csv", choices=("csv", "json"))
    a = p.parse_args(argv)
    asyncio.run(_record(a.subject, a.format))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
