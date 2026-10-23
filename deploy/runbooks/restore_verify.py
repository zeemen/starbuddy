#!/usr/bin/env python3
"""每周恢复演练的副本校验（LT-05 范围第 6 条，specs/launch-lt05-observability；手册 deploy/runbooks/weekly-restore-verify.md，由人执行）。

在“恢复到副本 → mark-replica 标记”之后，对**副本**做只读校验并输出报告（只有计数与校验和，不导出任何行内容）：
  1. 只连副本（复用迁移执行器 mark-replica 的限定，deploy/migrate/migrate.py）：连接串只从 --dsn-env 读、不带口令，变量名只收
     REPLICA_DSN；必须 --replica 与 --replica-host-allow，连接串里每个主机都必须是显式放行的副本主机（strict_replica_hosts）；
     连上后核对服务端地址是 RFC 1918 内网、且是放行主机解析出的地址（check_replica_server）；库里的标签必须已是 staging-replica
     （mark-replica 做过；主实例的标签是 staging，这里一律拒绝）；确认串带服务端地址，须逐字一致（--confirm）。
  2. 只读：每条查询各自包在一个只读事务里（BEGIN TRANSACTION READ ONLY → SET LOCAL row_security = off → 查询 → COMMIT；
     会话里另设 default_transaction_read_only = on 兜底）；不建表、不写任何东西。row_security = off：受行级安全约束的表不会被
     静默过滤成部分行，而是直接报错——所以校验角色必须不受 RLS 约束：gate 核对登录角色是超级用户或有 BYPASSRLS（推荐：由人建一个
     专用只读角色，LOGIN BYPASSRLS、只授 SELECT、default_transaction_read_only = on），或者是所有开了 RLS 的表的属主且这些表没有
     FORCE ROW LEVEL SECURITY；都不是就拒绝（未读数据）。
  3. 迁移账本：deploy_meta.schema_migrations 与仓库迁移文件的版本、顺序、sha256 一致（check_applied）。
  4. 每张表（platform、insight、kge、policy、deploy_meta，以及存在时的各科 schema aya、zhixing、wenli、echo 下的普通表与分区父表）：
     行数与内容校验和（md5(string_agg(md5(行文本) ORDER BY md5(行文本)))，与行顺序无关；只输出摘要，不输出行）。
  提示与确认串写标准错误（标准输出只留报告，便于重定向）。
  5. 与上一周的报告比较（--baseline）：上周有、这周没有的表，行数减少的表（--allow-decrease 显式列出的除外），迁移个数减少——
     一律算失败；行数不变而校验和变了只提示（staging 上有正常写入）。
报告写到 --out（不能已存在，权限 0600）或标准输出。退出码：0 通过；1 校验失败；2 参数、确认或前置核对不通过（未连库或未读数据）。
用法见手册；示例：
  python3 deploy/runbooks/restore_verify.py --dsn-env REPLICA_DSN --target-label staging-replica --replica \\
    --replica-host-allow <副本内网主机名> --confirm staging-replica:verify:<服务端地址> --baseline <上周报告.json> --out <本周报告.json>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "migrate"))
import migrate as M  # noqa: E402  —— 复用迁移执行器的连接串、副本主机与服务端地址核对

KIND = "starbuddy-restore-verify/1"
LABEL = M.REPLICA_TO          # staging-replica：mark-replica 之后的标签
DSN_ENV = "REPLICA_DSN"       # 演练手册约定：副本只用这个变量（主实例用 PRIMARY_DSN）
PLATFORM_SCHEMAS = ("deploy_meta", "insight", "kge", "platform", "policy")
SUBJECT_SCHEMAS = ("aya", "echo", "wenli", "zhixing")  # 各科私有 schema：存在才有表，不存在时 IN 列表里多出的名字不影响结果
SCHEMAS = PLATFORM_SCHEMAS + SUBJECT_SCHEMAS
_IN_SCHEMAS = "(" + ", ".join(M.sql_literal(s) for s in SCHEMAS) + ")"
TABLES_SQL = ("SELECT n.nspname, c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
              " WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition AND n.nspname IN " + _IN_SCHEMAS + " ORDER BY 1, 2")
# 校验角色能不能看到全部行：超级用户或 BYPASSRLS；否则数一数“开了 RLS、而本角色不是属主（或属主也被 FORCE）”的表
ROLE_SQL = "SELECT r.rolsuper::text, r.rolbypassrls::text FROM pg_roles r WHERE r.rolname = current_user"
RLS_BLOCKING_SQL = ("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                    " WHERE c.relkind IN ('r', 'p') AND n.nspname IN " + _IN_SCHEMAS + " AND c.relrowsecurity"
                    " AND (c.relowner <> (SELECT oid FROM pg_roles WHERE rolname = current_user) OR c.relforcerowsecurity)")
_IDENT = re.compile(r"^[a-z_][a-z0-9_$]{0,62}$")


class ReadOnlyPsql(M.Psql):
    """每条查询各自一个只读事务：BEGIN TRANSACTION READ ONLY、SET LOCAL row_security = off（受 RLS 约束就报错，不静默少行），
    会话另设 default_transaction_read_only = on 兜底；psql -q 不输出命令标签"""

    def query(self, sql: str) -> list[list[str]]:
        out = self._run(["-tA", "-F", "\t", "-c", "SET default_transaction_read_only = on", "-c", "BEGIN TRANSACTION READ ONLY",
                         "-c", "SET LOCAL row_security = off", "-c", sql, "-c", "COMMIT"])
        return [ln.split("\t") for ln in out.splitlines() if ln.strip()]

    def execute(self, sql: str) -> None:  # noqa: ARG002
        raise M.Failed("校验只读，不执行写入")


def quote_ident(name: str) -> str:
    if not _IDENT.fullmatch(name):
        raise M.Failed(f"表名不认识（{name[:40]!r}），拒绝拼进查询")
    return '"' + name + '"'


def table_sql(schema: str, table: str) -> str:
    q = f"{quote_ident(schema)}.{quote_ident(table)}"
    return (f"SELECT count(*), coalesce(md5(string_agg(md5(t::text), '' ORDER BY md5(t::text))), md5('')) FROM {q} t")


def gate(db: Any, allow: set[str], confirm: str | None) -> str:
    """mark-replica 同一套限定：标签已是 staging-replica；服务端地址是放行的副本主机；确认串带服务端地址、逐字一致。返回服务端地址"""
    label = M.target_label(db)
    if label != LABEL:
        raise M.Refused(f"只校验已标记的副本（库里标签必须是 {LABEL}，实际是 {label!r}；先用 mark-replica 标记副本）")
    rows = db.query("SELECT coalesce(host(inet_server_addr()), '')")
    addr = M.check_replica_server(rows[0][0] if rows else None, allow)
    check_role(db)
    token = f"{LABEL}:verify:{addr}"
    print(f"将只读校验副本（服务端 {addr}）", file=sys.stderr)
    print(f"确认串：--confirm {token}", file=sys.stderr)
    if confirm != token:
        raise M.Refused("确认串不一致：先核对服务端地址是副本，再以 --confirm 逐字给出确认串")
    return addr


def check_role(db: Any) -> None:
    """校验角色必须能看到全部行（否则行数与校验和会因 RLS 偏小、而看起来正常）：超级用户或 BYPASSRLS 直接通过；
    否则所有开了 RLS 的表都必须由本角色持有、且没有 FORCE ROW LEVEL SECURITY。都不满足就拒绝（还没读任何业务表）"""
    rows = db.query(ROLE_SQL)
    if not rows or len(rows[0]) != 2:
        raise M.Refused("读不到校验角色的属性")
    if "true" in rows[0]:
        return
    blocking = db.query(RLS_BLOCKING_SQL)
    n = blocking[0][0] if blocking else ""
    if n != "0":
        raise M.Refused(f"校验角色受行级安全约束（{n or '?'} 张开了 RLS 的表不归它所有或被 FORCE）：用 BYPASSRLS 的专用只读角色"
                        "（LOGIN BYPASSRLS、只授 SELECT、default_transaction_read_only = on），或表的属主")


def collect(db: Any, migs: list[M.Migration], addr: str) -> dict[str, Any]:
    done = M.applied(db)
    try:
        M.check_applied(migs, done)
    except M.Refused as e:
        raise M.Failed(f"迁移账本与仓库不一致：{e}") from None
    ledger = hashlib.sha256("".join(f"{v}:{sha}\n" for v, sha in sorted(done.items())).encode()).hexdigest()
    tables: dict[str, dict[str, Any]] = {}
    for schema, table in db.query(TABLES_SQL):
        rows = db.query(table_sql(schema, table))
        n, checksum = rows[0][0], rows[0][1]
        if not n.isdigit() or not re.fullmatch(r"[0-9a-f]{32}", checksum):
            raise M.Failed(f"{schema}.{table} 的计数或校验和格式不对")
        tables[f"{schema}.{table}"] = {"rows": int(n), "checksum": checksum}
    if not tables:
        raise M.Failed("副本上没有任何业务表（恢复不完整或连错了库）")
    return {"kind": KIND, "label": LABEL, "server": addr, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "migrations": {"count": len(done), "last": max(done) if done else None, "ledger_sha256": ledger}, "tables": tables}


def compare(report: dict[str, Any], baseline: dict[str, Any], allow_decrease: set[str]) -> tuple[list[str], list[str]]:
    """（失败，提示）"""
    if baseline.get("kind") != KIND:
        return [f"上周报告格式不对（kind 应为 {KIND}）"], []
    problems, notes = [], []
    cur, old = report["tables"], baseline.get("tables") or {}
    for t in sorted(set(old) - set(cur)):
        problems.append(f"{t}：上周有、这周没有")
    for t in sorted(set(cur) & set(old)):
        a, b = old[t]["rows"], cur[t]["rows"]
        if b < a and t not in allow_decrease:
            problems.append(f"{t}：行数从 {a} 减到 {b}（确属正常清理的，用 --allow-decrease 显式列出）")
        elif b == a and cur[t]["checksum"] != old[t]["checksum"]:
            notes.append(f"{t}：行数不变、内容校验和变了（有更新）")
    for t in sorted(set(cur) - set(old)):
        notes.append(f"{t}：新出现的表")
    if report["migrations"]["count"] < (baseline.get("migrations") or {}).get("count", 0):
        problems.append("迁移个数比上周少（副本恢复的时间点不对，或有迁移被回滚）")
    return problems, notes


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="restore_verify", description="每周恢复演练：只读校验副本的行数与校验和（只连副本）")
    p.add_argument("--dsn-env", default=DSN_ENV, help="存放副本连接串的环境变量名（只收 REPLICA_DSN）")
    p.add_argument("--target-label", required=True, help=f"必须是 {LABEL}")
    p.add_argument("--replica", action="store_true", help="声明目标是副本（必须给）")
    p.add_argument("--replica-host-allow", action="append", default=[], metavar="HOST", help="副本主机（必须给；连接串里每个主机都要在这里）")
    p.add_argument("--confirm", help="打印的确认串（带服务端地址），须逐字一致")
    p.add_argument("--baseline", help="上一周的报告（JSON），与之比较")
    p.add_argument("--allow-decrease", action="append", default=[], metavar="SCHEMA.TABLE", help="允许行数减少的表（正常清理，可重复）")
    p.add_argument("--out", help="报告写到这里（不能已存在）；不给就写标准输出")
    p.add_argument("--psql", help="psql 路径（缺省从 PATH 找）")
    p.add_argument("--migrations", default=str(M.MIGRATIONS), help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    try:
        if a.dsn_env != DSN_ENV:
            raise M.Refused(f"只收 --dsn-env {DSN_ENV}（主实例的 PRIMARY_DSN 一律拒绝）")
        if a.target_label != LABEL:
            raise M.Refused(f"--target-label 必须是 {LABEL}")
        if not (a.replica and a.replica_host_allow):
            raise M.Refused("必须同时给 --replica 与 --replica-host-allow（副本主机）")
        out = Path(a.out) if a.out else None
        if out is not None and out.exists():
            raise M.Refused("--out 已存在：不覆盖旧报告")
        baseline = json.loads(Path(a.baseline).read_text(encoding="utf-8")) if a.baseline else None
        migs = M.discover(Path(a.migrations))
        dsn = M.check_dsn(os.environ.get(a.dsn_env, ""))
        allow = set(a.replica_host_allow)
        M.strict_replica_hosts(dsn, allow)
        db = ReadOnlyPsql(dsn, a.psql)
        addr = gate(db, allow, a.confirm)
        report = collect(db, migs, addr)
        problems, notes = compare(report, baseline, set(a.allow_decrease)) if baseline is not None else ([], [])
        report["result"] = {"problems": problems, "notes": notes, "compared": baseline is not None}
        text = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
        if out is not None:
            fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
        else:
            sys.stdout.write(text)
        for n in notes:
            print(f"提示：{n}", file=sys.stderr)
        for x in problems:
            print(f"失败：{x}", file=sys.stderr)
        print(f"恢复校验：{len(report['tables'])} 张表、迁移 {report['migrations']['count']} 个；"
              + ("通过" if not problems else f"{len(problems)} 项失败"), file=sys.stderr)
        return 1 if problems else 0
    except M.Refused as e:
        print(f"restore_verify：拒绝（{e}）", file=sys.stderr)
        return 2
    except M.Failed as e:
        print(f"restore_verify：失败（{e}）", file=sys.stderr)
        return 1
    except (OSError, ValueError) as e:
        print(f"restore_verify：拒绝（读不了参数文件：{type(e).__name__}）", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
