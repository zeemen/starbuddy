"""LT-03 演练与回滚手册（deploy/runbooks）的静态核对：手册齐全、写明由人执行与两人复核；引用的迁移执行器子命令与参数真实存在、
确认串格式与执行器一致；连库一律 verify-full；只有占位、没有真实地址与凭据；演练对象是副本（-replica 标签 + --replica-host-allow）。"""
from __future__ import annotations

import re

import pytest

from conftest import DEPLOY

RB = DEPLOY / "runbooks"
MIGRATE = (DEPLOY / "migrate" / "migrate.py").read_text(encoding="utf-8")
DOCS = ["README.md", "rds-restore-and-migrate-rehearsal.md", "rollback.md", "weekly-restore-verify.md"]


def text(name: str) -> str:
    return (RB / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", DOCS)
def test_runbooks_exist_and_are_human_executed(name):
    t = text(name)
    assert "由人执行" in t or "由人" in t
    assert "复核" in t
    # 只有占位：没有真实主机名、IP（RFC 1918 网段说明除外）、账号 ID、凭据
    assert not re.search(r"[a-z0-9-]+\.(?:rds|pg)\.[a-z.]*aliyuncs\.com", t)
    assert not re.search(r"\b\d{16}\b|LTAI|password=|PGPASSWORD=", t)


def test_migrate_commands_match_executor():
    choices = re.search(r'choices=\(("plan"[^)]*)\)', MIGRATE).group(1)
    actions = set(x.strip().strip('"') for x in choices.split(","))
    assert "mark-replica" in actions
    flags = set(re.findall(r'add_argument\("(--[a-z-]+)"', MIGRATE))
    seen = 0
    for name in DOCS:
        for line in text(name).splitlines():
            m = re.search(r"migrate\.py ([a-z-]+)(.*)", line)
            if not m:
                continue
            seen += 1
            action, rest = m.group(1), m.group(2)
            assert action in actions, line
            for f in re.findall(r"(--[a-z-]+)", rest):
                assert f in flags, f"{name}：{f} 不是迁移执行器的参数"
            # 决定记录（续）第 5 条：主 / 副分用不同变量；副本命令带 --replica --replica-host-allow，主实例命令不带
            env = re.search(r"--dsn-env (\S+)", rest)
            assert env and env.group(1) in ("PRIMARY_DSN", "REPLICA_DSN"), f"{name}：迁移命令必须用 --dsn-env 选主 / 副：{line}"
            label = re.search(r"--target-label (\S+)", rest).group(1)
            if env.group(1) == "REPLICA_DSN":
                assert label == "staging-replica", line
                if action != "plan":
                    assert "--replica --replica-host-allow <副本内网主机名>" in rest, line
            else:
                assert label == "staging" and "--replica" not in rest and action in ("plan", "up", "down"), line
            c = re.search(r"--confirm (\S+)", rest)
            if c and c.group(1) != "x":
                parts = c.group(1).split(":")
                if action == "mark-replica":
                    assert parts[:3] == ["staging", "mark-replica", "staging-replica"] and parts[3] == "<服务端地址>", line
                else:
                    assert parts[1] == action and parts[0] == label and parts[2].startswith("<"), line
    assert seen >= 7


def test_rehearsal_targets_replica_with_tls():
    t = text("rds-restore-and-migrate-rehearsal.md")
    assert "--target-label staging-replica --replica" in t and "--replica-host-allow <副本内网主机名>" in t
    assert "恢复到新实例" in t and "不申请外网地址" in t and "按时间点" in t
    for dsn in re.findall(r"postgresql://\S+", t):
        assert "sslmode=verify-full" in dsn and "sslrootcert=" in dsn, dsn
    assert "mark-replica" in t and "UPDATE deploy_meta.target" not in t, "副本改标签只用执行器子命令，不手写 SQL"
    assert "主实例上永远不执行 mark-replica" in t
    assert "export REPLICA_DSN=" in t and "export PRIMARY_DSN=" in t and "unset REPLICA_DSN" in t
    assert "MIGRATE_DSN" not in t
    assert "探针" in t and "NetworkPolicy" in t, "探针与 NetworkPolicy 列为演练核对项"


# ---------------------------------------------------------------- LT-05 每周恢复校验（deploy/runbooks/restore_verify.py）
import importlib.util  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402

_spec = importlib.util.spec_from_file_location("restore_verify", RB / "restore_verify.py")
RV = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["restore_verify"] = RV
_spec.loader.exec_module(RV)
M = RV.M
import hashlib  # noqa: E402

SHA = {v: hashlib.sha256(f"-- {v}".encode()).hexdigest() for v in ("0001", "0002")}  # 与 _migs 写的合成迁移文件一致


class FakeDb:
    """按 SQL 内容回答的假副本（合成值）：标签、服务端地址、迁移账本、表清单、每张表的计数与校验和"""

    def __init__(self, label="staging-replica", addr="10.20.96.7", tables=None, ledger=None, role=("false", "true"), blocking="0") -> None:
        self.label, self.addr = label, addr
        self.tables = tables if tables is not None else {("platform", "account"): (3, "1" * 32), ("kge", "map_version"): (2, "2" * 32)}
        self.ledger = SHA if ledger is None else ledger
        self.role, self.blocking = role, blocking
        self.sql: list[str] = []

    def query(self, sql: str):
        self.sql.append(sql)
        if sql == RV.ROLE_SQL:
            return [list(self.role)]
        if sql == RV.RLS_BLOCKING_SQL:
            return [[self.blocking]]
        if "to_regclass" in sql:
            return [["t"]]
        if sql.startswith("SELECT label FROM deploy_meta.target"):
            return [[self.label]]
        if "inet_server_addr" in sql:
            return [[self.addr]]
        if "FROM deploy_meta.schema_migrations" in sql:
            return [[v, s] for v, s in sorted(self.ledger.items())]
        if sql == RV.TABLES_SQL:
            return [[s, t] for s, t in sorted(self.tables)]
        for (s, t), (n, c) in self.tables.items():
            if f'"{s}"."{t}"' in sql:
                return [[str(n), c]]
        raise AssertionError(sql)

    def execute(self, sql: str):
        raise AssertionError("校验不应写库")


def _migs(tmp_path):
    out = []
    for v in SHA:
        up, down = tmp_path / f"{v}_x.up.sql", tmp_path / f"{v}_x.down.sql"
        up.write_text(f"-- {v}", encoding="utf-8")
        down.write_text("--", encoding="utf-8")
        out.append(M.Migration(v, "x", up, down))
    return out


def test_restore_verify_gates_like_mark_replica(monkeypatch):
    monkeypatch.setattr(M.socket if hasattr(M, "socket") else __import__("socket"), "getaddrinfo",
                        lambda h, _p: [(0, 0, 0, "", ("10.20.96.7", 0))] if h == "replica.internal.invalid" else [])
    allow = {"replica.internal.invalid"}
    assert RV.gate(FakeDb(), allow, "staging-replica:verify:10.20.96.7") == "10.20.96.7"
    with pytest.raises(M.Refused):
        RV.gate(FakeDb(label="staging"), allow, "staging-replica:verify:10.20.96.7")  # 主实例的标签：拒绝
    with pytest.raises(M.Refused):
        RV.gate(FakeDb(), allow, "x")  # 确认串不对：只打印，不读表
    with pytest.raises(M.Refused):
        RV.gate(FakeDb(addr="10.20.96.99"), allow, "staging-replica:verify:10.20.96.99")  # 不是放行主机的地址
    with pytest.raises(M.Refused):
        RV.gate(FakeDb(addr="47.1.2.3"), allow, "staging-replica:verify:47.1.2.3")  # 公网地址
    with pytest.raises(M.Refused):
        M.strict_replica_hosts("postgresql://u@primary.internal.invalid/x", allow)
    # 安全审查 C：校验角色必须看得到全部行——超级用户或 BYPASSRLS；否则开了 RLS 的表都要归它所有且没有 FORCE
    ok = "staging-replica:verify:10.20.96.7"
    assert RV.gate(FakeDb(role=("true", "false")), allow, ok) == "10.20.96.7"
    assert RV.gate(FakeDb(role=("false", "false"), blocking="0"), allow, ok) == "10.20.96.7"
    for bad in (FakeDb(role=("false", "false"), blocking="3"), FakeDb(role=("false", "false"), blocking=""), FakeDb(role=())):
        with pytest.raises(M.Refused):
            RV.gate(bad, allow, ok)
        assert not any("string_agg" in q for q in bad.sql), "拒绝时还没读任何业务表"


def test_restore_verify_prompts_go_to_stderr_and_queries_are_read_only_transactions(monkeypatch, capsys):
    monkeypatch.setattr(M.socket if hasattr(M, "socket") else __import__("socket"), "getaddrinfo",
                        lambda h, _p: [(0, 0, 0, "", ("10.20.96.7", 0))] if h == "replica.internal.invalid" else [])
    RV.gate(FakeDb(), {"replica.internal.invalid"}, "staging-replica:verify:10.20.96.7")
    cap = capsys.readouterr()
    assert cap.out == "" and "确认串" in cap.err, "提示写标准错误，标准输出只留报告"
    calls: list[list[str]] = []

    class P(RV.ReadOnlyPsql):
        def __init__(self) -> None:  # 不找 psql、不连库
            pass

        def _run(self, args, *, stdin=None):
            calls.append(args)
            return "1\tx\n"
    assert P().query("SELECT 1") == [["1", "x"]]
    cs = [calls[0][i + 1] for i, a in enumerate(calls[0]) if a == "-c"]
    assert cs == ["SET default_transaction_read_only = on", "BEGIN TRANSACTION READ ONLY", "SET LOCAL row_security = off", "SELECT 1", "COMMIT"]
    with pytest.raises(M.Failed):
        P().execute("DELETE FROM x")
    assert set(RV.SUBJECT_SCHEMAS) == {"aya", "zhixing", "wenli", "echo"} and all(f"'{s}'" in RV.TABLES_SQL for s in RV.SCHEMAS)


def test_restore_verify_cli_refuses_before_connecting(monkeypatch, capsys):
    monkeypatch.setenv("PRIMARY_DSN", "postgresql://u@primary.internal.invalid/x")
    monkeypatch.setenv("REPLICA_DSN", "postgresql://u@replica.internal.invalid/x?sslmode=verify-full&sslrootcert=/x")
    base = ["--target-label", "staging-replica", "--replica", "--replica-host-allow", "replica.internal.invalid", "--psql", "/nonexistent/psql"]
    assert RV.main(["--dsn-env", "PRIMARY_DSN", *base]) == 2, "主实例变量一律拒绝"
    assert RV.main(["--dsn-env", "REPLICA_DSN", "--target-label", "staging", "--replica", "--replica-host-allow", "x"]) == 2
    assert RV.main(["--dsn-env", "REPLICA_DSN", "--target-label", "staging-replica"]) == 2, "没有 --replica 与放行主机"
    assert RV.main(["--dsn-env", "REPLICA_DSN", "--target-label", "staging-replica", "--replica", "--replica-host-allow", "other.invalid"]) == 2
    monkeypatch.setenv("REPLICA_DSN", "postgresql://u:pw@replica.internal.invalid/x")
    assert RV.main(["--dsn-env", "REPLICA_DSN", *base]) == 2, "连接串带口令拒绝"
    assert "pw" not in capsys.readouterr().err


def test_restore_verify_collects_counts_and_checksums_read_only(tmp_path):
    db = FakeDb()
    report = RV.collect(db, _migs(tmp_path), "10.20.96.7")
    assert report["tables"] == {"kge.map_version": {"rows": 2, "checksum": "2" * 32}, "platform.account": {"rows": 3, "checksum": "1" * 32}}
    assert report["migrations"]["count"] == 2 and report["migrations"]["last"] == "0002"
    assert all(q.lstrip().upper().startswith("SELECT") for q in db.sql), "只读：只有 SELECT"
    assert any("string_agg(md5(t::text), '' ORDER BY md5(t::text))" in q for q in db.sql), "校验和与行顺序无关"
    with pytest.raises(M.Failed):
        RV.collect(FakeDb(ledger={"0001": "f" * 64}), _migs(tmp_path), "10.20.96.7")  # 账本 sha256 与仓库不一致
    with pytest.raises(M.Failed):
        RV.collect(FakeDb(tables={}), _migs(tmp_path), "10.20.96.7")
    with pytest.raises(M.Failed):
        RV.quote_ident('x"; DROP TABLE y; --')


def test_restore_verify_compare_with_last_week(tmp_path):
    cur = RV.collect(FakeDb(), _migs(tmp_path), "10.20.96.7")
    same = json.loads(json.dumps(cur))
    assert RV.compare(cur, same, set()) == ([], [])
    old = json.loads(json.dumps(cur))
    old["tables"]["platform.account"]["rows"] = 5
    old["tables"]["platform.gone"] = {"rows": 1, "checksum": "3" * 32}
    problems, _ = RV.compare(cur, old, set())
    assert any("platform.account" in p and "减到" in p for p in problems) and any("platform.gone" in p for p in problems)
    problems, _ = RV.compare(cur, old, {"platform.account"})
    assert not any("platform.account" in p for p in problems)
    changed = json.loads(json.dumps(cur))
    changed["tables"]["kge.map_version"]["checksum"] = "9" * 32
    problems, notes = RV.compare(cur, changed, set())
    assert problems == [] and any("kge.map_version" in n for n in notes)
    fewer = json.loads(json.dumps(cur))
    fewer["migrations"]["count"] = 3
    assert RV.compare(cur, fewer, set())[0]
    assert RV.compare(cur, {"kind": "other"}, set())[0]


def test_weekly_runbook_uses_verify_script():
    t = text("weekly-restore-verify.md")
    assert "restore_verify.py --dsn-env REPLICA_DSN --target-label staging-replica --replica --replica-host-allow <副本内网主机名>" in t
    assert "--confirm staging-replica:verify:<服务端地址>" in t and "--baseline" in t and "PRIMARY_DSN" in t
    assert "不含任何行内容" in t and "每周" in t
    src = (RB / "restore_verify.py").read_text(encoding="utf-8")
    for needed in ("M.strict_replica_hosts(dsn, allow)", "M.check_replica_server(", "SET default_transaction_read_only = on",
                   "BEGIN TRANSACTION READ ONLY", "SET LOCAL row_security = off", "check_role(db)", "M.check_applied(migs, done)", "O_EXCL"):
        assert needed in src, needed


def test_rollback_covers_app_migration_pitr_and_iac():
    t = text("rollback.md")
    for must in ("kubectl -n starbuddy-staging set image", "@sha256:", "migrate.py down", "按时间点恢复", "sslmode=verify-full",
                 "删除保护", "Recreate", "/readyz"):
        assert must in t, must
