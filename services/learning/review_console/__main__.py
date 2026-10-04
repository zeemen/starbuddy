"""命令行：导出版本记录（spec 设计要点 11，Gate 0 证据“版本记录”），以及本地起审核台后端。

  python -m review_console serve --port 8790     只监听 127.0.0.1，不记访问日志；身份来自 staff 登录会话（PR 4）

  REVIEW_CONSOLE_ENV=dev REVIEW_CONSOLE_KGE_DATABASE_URL=... python -m review_console record --subject aya [--format csv|json]

只用 kge 连接，连上后先核对登录角色只继承 starbuddy_kge（安全审查 B-4）；只读 kge.version_record 与校验；输出到标准输出。

  python -m review_console reconcile-pages --export <合并记录.json> --staff-map <对应表.json>
      (--records <审核记录.json> | --db) [--out <报告.json>]

编译页面对账（冲刺 3，spec 第 6 节；格式见 services/learning/README.md“编译页面对账”）：不连托管平台。审核记录取自 --records 文件，
或 --db（REVIEW_CONSOLE_ENV 与 REVIEW_CONSOLE_KGE_DATABASE_URL，同样先核对只继承 starbuddy_kge，只读 kge.page_review_record）。
报告（kge-pages-reconcile/1）写到 --out（不能已存在）或标准输出；held_pages 是合并窗口要跳过的页面（knowledge.compiler --hold）。
退出码：0 对账通过；1 有失败（报告照常输出）；2 输入格式不对或读不了。
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys

import asyncpg

from . import pages
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


async def _db_page_records() -> list[dict]:
    if os.environ.get("REVIEW_CONSOLE_ENV") not in ALLOWED_ENVS:
        raise SystemExit(f"REVIEW_CONSOLE_ENV 必须是 {ALLOWED_ENVS} 之一")
    url = os.environ.get("REVIEW_CONSOLE_KGE_DATABASE_URL")
    if not url:
        raise SystemExit("缺少 REVIEW_CONSOLE_KGE_DATABASE_URL")
    conn = await asyncpg.connect(url)
    try:
        expect_role(await role_facts(conn), "starbuddy_kge")
        return await KgeStore(conn).page_records()  # type: ignore[arg-type]
    finally:
        await conn.close()


def reconcile_pages(export: str, staff_map: str, records: str | None, use_db: bool, out: str | None) -> int:
    try:
        exp = pages.parse_export(pages.load_json(export))
        smap = pages.parse_staff_map(pages.load_json(staff_map))
        recs = pages.records_from_rows(asyncio.run(_db_page_records())) if use_db else pages.parse_records(pages.load_json(records or ""))
    except pages.ReconcileInputError as e:
        print(f"编译页面对账：输入不对（{e}）", file=sys.stderr)
        return 2
    report = pages.reconcile(exp, recs, smap)
    text = pages.dumps(report)
    if out:
        try:
            with open(out, "x", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            print("编译页面对账：报告写不了（输出文件不能已存在）", file=sys.stderr)
            return 2
    else:
        sys.stdout.write(text)
    failed = sum(e["status"] == "failed" for e in report["merged"])
    print(f"编译页面对账：已合并 {len(report['merged'])} 页次，失败 {failed}，无合并记录的审核记录 {len(report['orphans'])}，"
          f"待审 {len(report['pending'])}；合并窗口跳过 {len(report['held_pages'])} 页", file=sys.stderr)
    return 0 if report["ok"] else 1


def _serve(port: int) -> None:
    import uvicorn

    from .app import create_app
    from .config import ReviewSettings

    uvicorn.run(create_app(ReviewSettings.from_env()), host="127.0.0.1", port=port, access_log=False, log_level="warning")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="review_console")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record", help="导出版本记录")
    r.add_argument("--subject", required=True, choices=SUBJECTS)
    r.add_argument("--format", default="csv", choices=("csv", "json"))
    s = sub.add_parser("serve", help="本地起审核台后端（只监听 127.0.0.1）")
    s.add_argument("--port", type=int, required=True)
    c = sub.add_parser("reconcile-pages", help="编译页面合并记录与审核记录对账（不连托管平台）")
    c.add_argument("--export", required=True, help="托管平台合并记录导出（kge-pages-merge-export/1）")
    c.add_argument("--staff-map", required=True, help="托管平台用户名 → staff 账号（kge-pages-staff-map/1）")
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument("--records", help="页面审核记录（kge-page-review-records/1）")
    g.add_argument("--db", action="store_true", help="从库读 kge.page_review_record（kge 连接）")
    c.add_argument("--out", help="报告文件（不能已存在；缺省写标准输出）")
    a = p.parse_args(argv)
    if a.cmd == "reconcile-pages":
        return reconcile_pages(a.export, a.staff_map, a.records, a.db, a.out)
    if a.cmd == "serve":
        _serve(a.port)
    else:
        asyncio.run(_record(a.subject, a.format))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
