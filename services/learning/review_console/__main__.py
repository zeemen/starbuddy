"""命令行：导出版本记录（spec 设计要点 11，Gate 0 证据“版本记录”）。

  REVIEW_CONSOLE_ENV=dev REVIEW_CONSOLE_KGE_DATABASE_URL=... python -m review_console record --subject aya [--format csv|json]

只用 kge 连接，连上后先核对登录角色只继承 starbuddy_kge（安全审查 B-4）；只读 kge.version_record 与校验；输出到标准输出。
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys

import asyncpg

from .app import SUBJECTS
from .config import ALLOWED_ENVS
from .diff import canonical_json
from .service import RECORD_COLUMNS, version_record
from .store import KgeStore, expect_role, role_facts


def write_records(rows: list[dict], fmt: str, out=sys.stdout) -> None:
    rows = [{c: r.get(c) for c in RECORD_COLUMNS} for r in rows]
    if fmt == "json":
        out.write(canonical_json(rows) + "\n")
        return
    w = csv.DictWriter(out, fieldnames=RECORD_COLUMNS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)


async def _record(subject: str, fmt: str) -> None:
    if os.environ.get("REVIEW_CONSOLE_ENV") not in ALLOWED_ENVS:
        raise SystemExit(f"REVIEW_CONSOLE_ENV 必须是 {ALLOWED_ENVS} 之一")
    url = os.environ.get("REVIEW_CONSOLE_KGE_DATABASE_URL")
    if not url:
        raise SystemExit("缺少 REVIEW_CONSOLE_KGE_DATABASE_URL")
    conn = await asyncpg.connect(url)
    try:
        expect_role(await role_facts(conn), "starbuddy_kge")
        write_records(await version_record(KgeStore(conn), subject), fmt)  # type: ignore[arg-type]
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
