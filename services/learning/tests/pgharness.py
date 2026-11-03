"""一次性的本地 Postgres（做法同 db/tests/run.sh）：Unix socket、不监听 TCP、结束即删除。
只连本机临时实例，不连接任何共享或生产库（CLAUDE.md 规则 9）。
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LOGIN_ROLE = "learning_app"  # 只继承 starbuddy_learning（spec 范围第 7 条）


def find_pgbin() -> str | None:
    if os.environ.get("PGBIN"):
        return os.environ["PGBIN"]
    if os.path.exists("/usr/lib/postgresql/16/bin/initdb"):
        return "/usr/lib/postgresql/16/bin"
    found = sorted(glob.glob("/usr/lib/postgresql/*/bin"))
    return found[-1] if found else None


class EphemeralPG:
    def __init__(self) -> None:
        pgbin = find_pgbin()
        if not pgbin or not os.path.exists(f"{pgbin}/initdb"):
            raise FileNotFoundError("本机没有 Postgres 服务端（需要 postgresql-16）")
        self.pgbin = pgbin
        self.root = os.geteuid() == 0
        self.work = tempfile.mkdtemp(prefix="starbuddy-learning-test.")
        if self.root:
            shutil.chown(self.work, "postgres")
        os.chmod(self.work, 0o700)
        self.db = "starbuddy_test"  # 与合成种子、诊断合成题面的库名核对一致（starbuddy_(dev|test)）

    def _as_pg(self, *args: str, **kw) -> subprocess.CompletedProcess:
        cmd = (["runuser", "-u", "postgres", "--"] if self.root else []) + list(args)
        return subprocess.run(cmd, check=True, capture_output=True, **kw)

    def start(self) -> None:
        self._as_pg(f"{self.pgbin}/initdb", "-D", f"{self.work}/data", "-A", "trust", "-U", "postgres", "-E", "UTF8", "--no-sync")
        self._as_pg(
            f"{self.pgbin}/pg_ctl", "-D", f"{self.work}/data", "-w", "-l", f"{self.work}/pg.log",
            "-o", f"-k {self.work} -c listen_addresses='' -c fsync=off -c timezone=UTC -c max_connections=100",
            "start",
        )
        self._as_pg(f"{self.pgbin}/createdb", "-h", self.work, "-U", "postgres", self.db)
        self.psql_file(ROOT / "db/bootstrap/roles.sql")
        for f in sorted((ROOT / "db/migrations").glob("*.up.sql")):
            self.psql_file(f)
        self.psql(f"CREATE ROLE {LOGIN_ROLE} LOGIN IN ROLE starbuddy_learning;")

    def psql(self, sql: str) -> str:
        r = self._as_pg(
            f"{self.pgbin}/psql", "-h", self.work, "-U", "postgres", "-d", self.db, "-X", "-q", "-tA",
            "-v", "ON_ERROR_STOP=1", input=sql.encode(),
        )
        return r.stdout.decode()

    def psql_file(self, path: Path) -> None:
        self.psql(path.read_text(encoding="utf-8"))

    def dsn(self, user: str) -> dict:
        return {"host": self.work, "user": user, "database": self.db}

    def stop(self) -> None:
        try:
            self._as_pg(f"{self.pgbin}/pg_ctl", "-D", f"{self.work}/data", "-m", "immediate", "stop")
        except subprocess.CalledProcessError:
            pass
        shutil.rmtree(self.work, ignore_errors=True)
