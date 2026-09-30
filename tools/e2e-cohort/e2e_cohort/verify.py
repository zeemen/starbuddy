"""真实内测的只读核对（RUNBOOK.md）：同一套断言，只输出汇总。本仓 CI 只用合成库测试它的护栏与汇总形状。

护栏（任何一条不满足就拒绝，返回 2，不写任何输出）：
- 连接串只从环境变量读；学生名单每行一个 UUID；
- 连上的库名必须等于 --expect-db（防止连错库）；
- 登录角色不能是超级用户；会话必须是只读（transaction_read_only = on）；
- 输出目录解析后不能落在仓库根目录之内（汇总不进仓库）。
名单里有学生找不到 → 返回 1（汇总照写，便于排查名单）；任一断言失败 → 返回 1。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import asyncpg

from . import checks as C

ROOT = Path(__file__).resolve().parents[3]
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class Refused(Exception):
    pass


def out_dir_allowed(out: Path) -> bool:
    resolved = out.expanduser().resolve()
    return not (resolved == ROOT or ROOT in resolved.parents)


def read_students(path: Path) -> list[str]:
    ids = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
    if not ids or not all(UUID.match(i) for i in ids) or len(set(ids)) != len(ids):
        raise Refused("学生名单只能是每行一个不重复的 student_id（小写 UUID）")
    return ids


async def preflight(conn, expect_db: str) -> None:
    db = await conn.fetchval("SELECT current_database()")
    if db != expect_db:
        raise Refused(f"连上的库不是 --expect-db 指定的库（实际 {db}）")
    if await conn.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname = current_user"):
        raise Refused("核对登录不能是超级用户")
    if await conn.fetchval("SELECT current_setting('transaction_read_only')") != "on":
        raise Refused("会话不是只读（transaction_read_only 应为 on）")


async def run_verify(dsn: str, students: list[str], authorization_ref: str, out: Path, expect_db: str) -> tuple[int, dict | None]:
    if not out_dir_allowed(out):
        raise Refused("输出目录在仓库之内：汇总不进仓库")
    conn = await asyncpg.connect(dsn, server_settings={"default_transaction_read_only": "on"})
    try:
        await preflight(conn, expect_db)
        pop = await C.load_population(conn, students)
        results = [await C.check_coexistence(conn, pop), await C.check_no_private_copies(conn, pop),
                   await C.check_grade_rows(conn, pop), await C.check_isolation(conn, pop),
                   await C.check_guardian_links_backed(conn, pop)]
        events = await C.event_counts(conn, pop)
    finally:
        await conn.close()
    by_subject: dict[str, int] = {}
    for e in events:
        by_subject[e["subject"]] = by_subject.get(e["subject"], 0) + e["count"]
    detail = {c.name: c.detail for c in results}
    summary = {
        "notice": "真实内测只读核对的汇总（不含任何学生级明细）",
        "authorization_ref": authorization_ref,
        "students_listed": len(students), "students_found": len(pop.members),
        "evidence_events_by_subject": dict(sorted(by_subject.items())),
        "checks": [{"name": c.name, "passed": c.passed} for c in results],
        "coexistence_students_with_all_core_subjects": detail["coexistence"]["students_with_all_core_subjects"],
        "isolation_table_source": detail["isolation_rls"]["table_source"],
        "isolation_tables_compared": detail["isolation_rls"]["tables_compared"],
        "isolation_violations": len(detail["isolation_rls"]["violations"]),
        "private_copy_registry": detail["no_private_copies"]["registry"],
        "guardian_links_unbacked": detail["guardian_links_backed"]["unbacked_active_links"],
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "pilot_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    ok = all(c.passed for c in results) and len(pop.members) == len(students)
    return (0 if ok else 1), summary


async def main(dsn_env: str, students_file: Path, authorization_ref: str, out: Path, expect_db: str) -> int:
    dsn = os.environ.get(dsn_env, "")
    try:
        if not dsn:
            raise Refused(f"环境变量 {dsn_env} 未设置（连接串不写进命令行与仓库）")
        rc, _ = await run_verify(dsn, read_students(students_file), authorization_ref, out, expect_db)
    except Refused as e:
        print(f"verify: 拒绝执行：{e}")
        return 2
    print(f"verify: 已写入 {out / 'pilot_summary.json'}（{'通过' if rc == 0 else '有失败或名单不全'}）")
    return rc
