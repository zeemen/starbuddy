#!/usr/bin/env python3
"""迁移执行器（LT-01 范围第 4 条，specs/launch-lt01-deploy-packaging）。只用标准库与 psql 命令行；不内置任何连接串。

  python deploy/migrate/migrate.py plan     --target-label <标签> [--dsn-env MIGRATE_DSN]
  python deploy/migrate/migrate.py up       --target-label <标签> --confirm <标签>:up:<个数>:<最后版本> [--with-bootstrap]
  python deploy/migrate/migrate.py down     --target-label <标签> --to <版本或 0000> --confirm <标签>:down:<个数>:<目标版本>
  python deploy/migrate/migrate.py rehearse --target-label <标签>-replica --replica --confirm <标签>:rehearse:<个数>:<最后版本> [--with-bootstrap]

规则：
- 连接串只从环境变量读（缺省 MIGRATE_DSN，--dsn-env 换名），不收命令行里的连接串；连接串里带口令拒绝（口令走 PGPASSFILE 或 libpq 的
  密钥文件，由人配置）。执行器从不打印连接串。
- 按文件名顺序执行 db/migrations/NNNN_*.up.sql（down 逆序）；每个迁移与版本表的一行在同一个事务里（psql -1），失败即停、不继续后面的。
- 版本表 deploy_meta.schema_migrations（版本、名字、up 文件 sha256、执行时间）；deploy_meta.target 记录第一次执行时的目标标签，
  之后标签不一致拒绝执行（防止连错库）。已执行的迁移文件被改过（sha256 不一致）拒绝执行。
- 执行前打印将执行的迁移清单（版本、名字、sha256 前 12 位）与确认串；--confirm 必须与之逐字一致才执行（显式确认参数）。
- rehearse（副本演练）：只在 --replica 且标签以 -replica 结尾时可用，在快照副本上 up → 校验 → down 回到演练前的基线 → 校验 → up → 校验。
- --with-bootstrap：先执行 db/bootstrap/roles.sql（集群级角色；只用于一次性库或副本，正式环境由人单独执行）。
- rehearse 与 --with-bootstrap 只连 Unix socket 或回环（安全审查 S9）；别的副本主机须用 --replica-host-allow 显式放行。
- 目标库已有 platform schema 却没有 deploy_meta 版本表（不是本执行器管理的库）：plan 只提示，up / down / rehearse 拒绝。
退出码：0 成功；1 执行或校验失败；2 参数、确认或前置核对不通过（未连库或未改库）。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "db" / "migrations"
BOOTSTRAP = ROOT / "db" / "bootstrap" / "roles.sql"
LABEL = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
FILE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.(up|down)\.sql$")
CORE_SCHEMAS = ("platform", "insight", "kge", "policy")
META_DDL = """
CREATE SCHEMA IF NOT EXISTS deploy_meta;
CREATE TABLE IF NOT EXISTS deploy_meta.schema_migrations (
  version text PRIMARY KEY CHECK (version ~ '^[0-9]{4}$'),
  name text NOT NULL,
  up_sha256 text NOT NULL CHECK (up_sha256 ~ '^[0-9a-f]{64}$'),
  applied_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS deploy_meta.target (
  only_row boolean PRIMARY KEY DEFAULT true CHECK (only_row),
  label text NOT NULL
);
"""


class Refused(Exception):
    """参数、确认或前置核对不通过（退出码 2）"""


class Failed(Exception):
    """执行或校验失败（退出码 1）"""


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    up: Path
    down: Path

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.up.read_bytes()).hexdigest()


def discover(directory: Path = MIGRATIONS) -> list[Migration]:
    """成对的 up / down 文件，版本唯一且连续递增；拒绝符号链接与不认识的文件名"""
    ups: dict[str, tuple[str, Path]] = {}
    downs: dict[str, tuple[str, Path]] = {}
    for p in sorted(directory.iterdir()):
        if p.name.startswith(".") or p.suffix != ".sql":
            continue
        if p.is_symlink() or not p.is_file():
            raise Refused(f"迁移文件不能是符号链接或非普通文件：{p.name}")
        m = FILE.match(p.name)
        if not m:
            raise Refused(f"不认识的迁移文件名：{p.name}")
        target = ups if m.group(3) == "up" else downs
        if m.group(1) in target:
            raise Refused(f"版本 {m.group(1)} 有重复的 {m.group(3)} 文件")
        target[m.group(1)] = (m.group(2), p)
    if set(ups) != set(downs):
        raise Refused(f"up 与 down 不成对：{sorted(set(ups) ^ set(downs))}")
    out = []
    for i, v in enumerate(sorted(ups)):
        if int(v) != i + 1:
            raise Refused(f"版本不连续：第 {i + 1} 个是 {v}")
        if ups[v][0] != downs[v][0]:
            raise Refused(f"版本 {v} 的 up 与 down 名字不一致")
        out.append(Migration(v, ups[v][0], ups[v][1], downs[v][1]))
    return out


def check_dsn(dsn: str) -> str:
    """连接串：postgresql:// 或 key=value；不收口令（URL 里的 user:pass@ 或 password= 参数）"""
    if not dsn:
        raise Refused("没有连接串：在环境变量里设置（缺省 MIGRATE_DSN），执行器不内置任何连接串")
    if dsn.startswith(("postgresql://", "postgres://")):
        u = urlsplit(dsn)
        if u.password is not None or "password" in parse_qs(u.query):
            raise Refused("连接串里不能带口令（口令用 PGPASSFILE 等由人配置）")
    elif re.search(r"(^|\s)password\s*=", dsn):
        raise Refused("连接串里不能带口令（口令用 PGPASSFILE 等由人配置）")
    elif "=" not in dsn:
        raise Refused("连接串必须是 postgresql:// 或 key=value 形式")
    return dsn


def is_replica_label(label: str) -> bool:
    return label.endswith("-replica")


def dsn_hosts(dsn: str) -> list[str]:
    """连接串里的全部主机（URL 的主机部分按逗号拆、host 查询参数；key=value 的 host=）；没写主机 = libpq 缺省的 Unix socket"""
    hosts: list[str] = []
    if dsn.startswith(("postgresql://", "postgres://")):
        u = urlsplit(dsn)
        for part in u.netloc.rsplit("@", 1)[-1].split(","):
            h = part[1:part.find("]")] if part.startswith("[") else (part.rsplit(":", 1)[0] if part.count(":") == 1 else part)
            if h:
                hosts.append(unquote(h))
        for v in parse_qs(u.query).get("host", []):
            hosts += [h for h in v.split(",") if h]
        if "hostaddr" in parse_qs(u.query):
            hosts.append("<hostaddr>")
    else:
        for m in re.finditer(r"(?:^|\s)(host|hostaddr)\s*=\s*(\S+)", dsn):
            hosts += ["<hostaddr>"] if m.group(1) == "hostaddr" else [h for h in m.group(2).split(",") if h]
    return hosts or ["/"]


def local_only_hosts(dsn: str, allow: set[str]) -> None:
    """副本演练与 --with-bootstrap 只连 Unix socket 或回环（安全审查 S9）；--replica-host-allow 显式放行的主机除外"""
    import ipaddress
    for h in dsn_hosts(dsn):
        if h.startswith("/") or h == "localhost" or h in allow:
            continue
        try:
            if ipaddress.ip_address(h).is_loopback:
                continue
        except ValueError:
            pass
        raise Refused("副本演练与 --with-bootstrap 只连 Unix socket 或回环地址（其他主机须用 --replica-host-allow 显式放行）")


class Psql:
    """psql 命令行：-X 不读 psqlrc，ON_ERROR_STOP，单事务；输出不含连接串"""

    def __init__(self, dsn: str, psql: str | None = None) -> None:
        self.dsn = dsn
        self.bin = psql or shutil.which("psql") or ""
        if not self.bin:
            raise Refused("找不到 psql（用 --psql 指定路径）")

    def _run(self, args: list[str], *, stdin: str | None = None) -> str:
        env = {k: v for k, v in os.environ.items() if not k.startswith("PG") or k in ("PGPASSFILE", "PGSSLMODE", "PGSSLROOTCERT", "PGCONNECT_TIMEOUT")}
        env["PGOPTIONS"] = "-c client_min_messages=warning"
        r = subprocess.run([self.bin, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", self.dsn, *args], input=stdin, capture_output=True,
                           text=True, env=env, check=False)
        if r.returncode != 0:
            tail = (r.stderr.strip().splitlines() or ["(无输出)"])[-1]
            raise Failed(tail.replace(self.dsn, "<连接串>")[:300])
        return r.stdout

    def query(self, sql: str) -> list[list[str]]:
        out = self._run(["-tA", "-F", "\t", "-c", sql])
        return [ln.split("\t") for ln in out.splitlines() if ln.strip()]

    def execute(self, sql: str) -> None:
        self._run(["-1"], stdin=sql)

    def apply(self, path: Path, after_sql: str) -> None:
        """一个文件与一段记账 SQL 在同一个事务里"""
        self._run(["-1", "-f", str(path), "-c", after_sql])


def sql_literal(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def ensure_meta(db: Psql, label: str) -> None:
    db.execute(META_DDL)
    rows = db.query("SELECT label FROM deploy_meta.target")
    if not rows:
        db.execute(f"INSERT INTO deploy_meta.target (label) VALUES ({sql_literal(label)}) ON CONFLICT DO NOTHING;")
    elif rows[0][0] != label:
        raise Refused("目标库记录的标签与 --target-label 不一致：可能连错了库，拒绝执行")


def applied(db: Psql) -> dict[str, str]:
    exists = db.query("SELECT to_regclass('deploy_meta.schema_migrations') IS NOT NULL")
    if not exists or exists[0][0] != "t":
        return {}
    return {v: sha for v, sha in db.query("SELECT version, up_sha256 FROM deploy_meta.schema_migrations ORDER BY version")}


def unmanaged(db: Psql) -> bool:
    """目标库已有 platform schema、却没有执行器的版本表：不是本执行器管理的库（安全审查 S9）"""
    rows = db.query("SELECT to_regnamespace('platform') IS NOT NULL, to_regclass('deploy_meta.schema_migrations') IS NOT NULL")
    return bool(rows) and rows[0][0] == "t" and rows[0][1] != "t"


def target_label(db: Psql) -> str | None:
    exists = db.query("SELECT to_regclass('deploy_meta.target') IS NOT NULL")
    if not exists or exists[0][0] != "t":
        return None
    rows = db.query("SELECT label FROM deploy_meta.target")
    return rows[0][0] if rows else None


def check_applied(migs: list[Migration], done: dict[str, str]) -> None:
    """已执行的必须是前缀、且文件没被改过"""
    by_v = {m.version: m for m in migs}
    for v, sha in done.items():
        if v not in by_v:
            raise Refused(f"库里有仓库没有的迁移 {v}")
        if by_v[v].sha256 != sha:
            raise Refused(f"迁移 {v} 执行后文件被改过（sha256 不一致）")
    expected = [m.version for m in migs[: len(done)]]
    if sorted(done) != expected:
        raise Refused("库里已执行的迁移不是仓库迁移的前缀（有跳过的版本）")


def confirm_token(label: str, action: str, count: int, last: str) -> str:
    return f"{label}:{action}:{count}:{last}"


def print_plan(title: str, items: list[Migration], token: str, out=sys.stdout) -> None:
    out.write(f"{title}（{len(items)} 个）：\n")
    for m in items:
        out.write(f"  {m.version}  {m.name}  sha256:{m.sha256[:12]}\n")
    out.write(f"确认串：--confirm {token}\n")


def up_plan(migs: list[Migration], done: dict[str, str]) -> list[Migration]:
    return migs[len(done):]


def down_plan(migs: list[Migration], done: dict[str, str], to: str) -> list[Migration]:
    if not re.fullmatch(r"\d{4}", to):
        raise Refused("--to 必须是四位版本号（0000 = 全部回滚）")
    if to != "0000" and to not in done:
        raise Refused(f"--to {to} 不是已执行的版本")
    return [m for m in reversed(migs[: len(done)]) if m.version > to]


def run_up(db: Psql, items: list[Migration]) -> None:
    for m in items:
        rec = (f"INSERT INTO deploy_meta.schema_migrations (version, name, up_sha256) VALUES "
               f"({sql_literal(m.version)}, {sql_literal(m.name)}, {sql_literal(m.sha256)});")
        try:
            db.apply(m.up, rec)
        except Failed as e:
            raise Failed(f"迁移 {m.version}_{m.name} 执行失败（已回滚本迁移，之后的未执行）：{e}") from None
        print(f"up   {m.version}  {m.name}")


def run_down(db: Psql, items: list[Migration]) -> None:
    for m in items:
        try:
            db.apply(m.down, f"DELETE FROM deploy_meta.schema_migrations WHERE version = {sql_literal(m.version)};")
        except Failed as e:
            raise Failed(f"迁移 {m.version}_{m.name} 回滚失败（已回滚本步，之前的保持原样）：{e}") from None
        print(f"down {m.version}  {m.name}")


def verify(db: Psql, migs: list[Migration], expect_count: int) -> None:
    """版本表与仓库一致：条数、顺序、sha256；全部回滚时核心 schema 不存在，有迁移时 platform 存在"""
    done = applied(db)
    if len(done) != expect_count:
        raise Failed(f"校验失败：版本表有 {len(done)} 条，应为 {expect_count}")
    try:
        check_applied(migs, done)
    except Refused as e:
        raise Failed(f"校验失败：{e}") from None
    names = {r[0] for r in db.query("SELECT nspname FROM pg_namespace")}
    if expect_count == 0 and names & set(CORE_SCHEMAS):
        raise Failed(f"校验失败：全部回滚后仍有 {sorted(names & set(CORE_SCHEMAS))}")
    if expect_count > 0 and "platform" not in names:
        raise Failed("校验失败：执行过迁移但没有 platform schema")
    print(f"校验通过：版本表 {expect_count} 条与仓库一致")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="migrate", description="StarBuddy 迁移执行器（不内置连接串；执行前须显式确认）")
    p.add_argument("action", choices=("plan", "up", "down", "rehearse"))
    p.add_argument("--target-label", required=True, help="目标库标签（小写字母、数字、连字符；副本须以 -replica 结尾）")
    p.add_argument("--dsn-env", default="MIGRATE_DSN", help="存放连接串的环境变量名（缺省 MIGRATE_DSN）")
    p.add_argument("--confirm", help="plan 打印的确认串，须逐字一致")
    p.add_argument("--to", help="down：回滚到这个版本（不含），0000 = 全部")
    p.add_argument("--replica", action="store_true", help="rehearse：声明目标是快照副本或一次性库")
    p.add_argument("--with-bootstrap", action="store_true", help="先执行 db/bootstrap/roles.sql（只用于一次性库或副本）")
    p.add_argument("--psql", help="psql 路径（缺省从 PATH 找）")
    p.add_argument("--replica-host-allow", action="append", default=[], metavar="HOST",
                   help="rehearse / --with-bootstrap 另外放行的副本主机（缺省只收 Unix socket 与回环；可重复）")
    p.add_argument("--migrations", default=str(MIGRATIONS), help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    try:
        if not LABEL.match(a.target_label):
            raise Refused("--target-label 只收小写字母、数字与连字符（2–41 个字符）")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", a.dsn_env):
            raise Refused("--dsn-env 必须是环境变量名")
        if a.action == "rehearse" and not (a.replica and is_replica_label(a.target_label)):
            raise Refused("副本演练只在 --replica 且标签以 -replica 结尾时可用")
        if a.with_bootstrap and not (a.replica and is_replica_label(a.target_label)):
            raise Refused("--with-bootstrap 只用于副本或一次性库（--replica 且标签以 -replica 结尾）；正式环境的角色由人单独执行")
        migs = discover(Path(a.migrations))
        dsn = check_dsn(os.environ.get(a.dsn_env, ""))
        if a.action == "rehearse" or a.with_bootstrap:
            local_only_hosts(dsn, set(a.replica_host_allow))
        db = Psql(dsn, a.psql)
        if unmanaged(db):
            if a.action != "plan":
                raise Refused("目标库已有 platform schema 却没有 deploy_meta 版本表：不是本执行器管理的库，拒绝执行（由人决定如何接管）")
            print("注意：目标库已有 platform schema 却没有 deploy_meta 版本表（不是本执行器管理的库；up / down / rehearse 会拒绝）")
        done = applied(db)
        check_applied(migs, done)
        label_in_db = target_label(db)
        if label_in_db is not None and label_in_db != a.target_label:
            raise Refused("目标库记录的标签与 --target-label 不一致：可能连错了库，拒绝执行")
        print(f"目标：{a.target_label}；已执行 {len(done)} 个，仓库共 {len(migs)} 个")
        if a.action == "plan":
            todo = up_plan(migs, done)
            print_plan("将执行的迁移（up）", todo, confirm_token(a.target_label, "up", len(todo), todo[-1].version if todo else "none"))
            if is_replica_label(a.target_label):
                print(f"副本演练确认串：--confirm {confirm_token(a.target_label, 'rehearse', len(todo), todo[-1].version if todo else 'none')}")
            return 0
        if a.action in ("up", "rehearse"):
            todo = up_plan(migs, done)
            token = confirm_token(a.target_label, a.action, len(todo), todo[-1].version if todo else "none")
            print_plan("将执行的迁移（up）", todo, token)
            if a.confirm != token:
                raise Refused("确认串不一致：先核对上面的清单，再以 --confirm 逐字给出确认串")
            if a.with_bootstrap:
                db.execute(BOOTSTRAP.read_text(encoding="utf-8"))
                print("bootstrap：角色已核对")
            ensure_meta(db, a.target_label)
            baseline = len(done)
            run_up(db, todo)
            verify(db, migs, baseline + len(todo))
            if a.action == "rehearse":
                back = [m for m in reversed(migs[: baseline + len(todo)]) if int(m.version) > baseline]
                run_down(db, back)
                verify(db, migs, baseline)
                run_up(db, todo)
                verify(db, migs, baseline + len(todo))
                print(f"副本演练通过：up → 校验 → down（回到基线 {baseline}）→ 校验 → up → 校验")
            return 0
        if a.to is None:
            raise Refused("down 需要 --to")
        todo = down_plan(migs, done, a.to)
        token = confirm_token(a.target_label, "down", len(todo), a.to)
        print_plan("将回滚的迁移（down，逆序）", todo, token)
        if a.confirm != token:
            raise Refused("确认串不一致：先核对上面的清单，再以 --confirm 逐字给出确认串")
        run_down(db, todo)
        verify(db, migs, len(done) - len(todo))
        return 0
    except Refused as e:
        print(f"migrate：拒绝（{e}）", file=sys.stderr)
        return 2
    except Failed as e:
        print(f"migrate：失败（{e}）", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
