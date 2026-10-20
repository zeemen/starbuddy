"""迁移执行器的单测（不连库）：文件发现与成对核对、连接串口令拒绝、确认串、回滚清单、已执行前缀与改动检测、参数前置拒绝。
连库的端到端（副本演练在一次性库上）见 deploy/migrate/rehearse-ephemeral.sh（make deploy-check 调用）。"""
from __future__ import annotations

import pytest

import migrate as M


def write(d, names):
    for n in names:
        (d / n).write_text(f"-- {n}\n", encoding="utf-8")


def test_discover_repo_migrations():
    migs = M.discover()
    assert migs and migs[0].version == "0001" and all(int(m.version) == i + 1 for i, m in enumerate(migs))
    assert all(len(m.sha256) == 64 for m in migs)


@pytest.mark.parametrize("names,msg", [
    (["0001_a.up.sql"], "不成对"),
    (["0001_a.up.sql", "0001_a.down.sql", "0003_c.up.sql", "0003_c.down.sql"], "不连续"),
    (["0001_a.up.sql", "0001_b.down.sql"], "名字不一致"),
    (["0001_A.up.sql", "0001_A.down.sql"], "不认识"),
])
def test_discover_rejects(tmp_path, names, msg):
    write(tmp_path, names)
    with pytest.raises(M.Refused, match=msg):
        M.discover(tmp_path)


def test_discover_rejects_symlink(tmp_path):
    write(tmp_path, ["0001_a.up.sql"])
    (tmp_path / "0001_a.down.sql").symlink_to(tmp_path / "0001_a.up.sql")
    with pytest.raises(M.Refused, match="符号链接"):
        M.discover(tmp_path)


@pytest.mark.parametrize("dsn", ["postgresql://u:pw@10.0.0.5/db", "postgresql://u@10.0.0.5/db?password=pw", "host=x password=pw dbname=y",
                                 "", "just-a-name"])
def test_dsn_refused(dsn):
    with pytest.raises(M.Refused):
        M.check_dsn(dsn)


@pytest.mark.parametrize("dsn", ["postgresql://u@/db?host=/tmp/x", "postgresql://migrator@172.28.0.20:5432/starbuddy", "host=/tmp/x dbname=y user=z"])
def test_dsn_ok(dsn):
    assert M.check_dsn(dsn) == dsn


def test_plans_and_tokens(tmp_path):
    write(tmp_path, [f"{i:04d}_m{i}.{d}.sql" for i in range(1, 6) for d in ("up", "down")])
    migs = M.discover(tmp_path)
    done = {m.version: m.sha256 for m in migs[:3]}
    M.check_applied(migs, done)
    assert [m.version for m in M.up_plan(migs, done)] == ["0004", "0005"]
    assert [m.version for m in M.down_plan(migs, done, "0001")] == ["0003", "0002"]
    assert [m.version for m in M.down_plan(migs, done, "0000")] == ["0003", "0002", "0001"]
    with pytest.raises(M.Refused):
        M.down_plan(migs, done, "0004")
    with pytest.raises(M.Refused):
        M.down_plan(migs, done, "1")
    assert M.confirm_token("lt-replica", "up", 2, "0005") == "lt-replica:up:2:0005"


def test_check_applied_detects_changes_and_gaps(tmp_path):
    write(tmp_path, [f"{i:04d}_m{i}.{d}.sql" for i in range(1, 4) for d in ("up", "down")])
    migs = M.discover(tmp_path)
    with pytest.raises(M.Refused, match="改过"):
        M.check_applied(migs, {"0001": "0" * 64})
    with pytest.raises(M.Refused, match="前缀"):
        M.check_applied(migs, {"0002": migs[1].sha256})
    with pytest.raises(M.Refused, match="仓库没有"):
        M.check_applied(migs, {"0009": "0" * 64})


@pytest.mark.parametrize("argv", [
    ["plan", "--target-label", "Bad_Label"],
    ["rehearse", "--target-label", "staging-cn", "--replica", "--confirm", "x"],
    ["rehearse", "--target-label", "staging-cn-replica", "--confirm", "x"],
    ["up", "--target-label", "staging-cn", "--with-bootstrap", "--confirm", "x"],
    ["plan", "--target-label", "staging-cn", "--dsn-env", "lower"],
])
def test_argument_refusals_before_connecting(argv, monkeypatch, capsys):
    monkeypatch.setenv("MIGRATE_DSN", "postgresql://u@/db?host=/nonexistent")
    assert M.main(argv) == 2
    assert "postgresql://" not in capsys.readouterr().err


def test_missing_dsn_refused(monkeypatch, capsys):
    monkeypatch.delenv("MIGRATE_DSN", raising=False)
    assert M.main(["plan", "--target-label", "staging-cn"]) == 2
    assert "不内置任何连接串" in capsys.readouterr().err


@pytest.mark.parametrize("dsn,ok", [
    ("postgresql://u@/db?host=/tmp/x", True), ("postgresql://u@localhost/db", True), ("postgresql://u@127.0.0.1:5432/db", True),
    ("postgresql://u@[::1]:5432/db", True), ("host=/tmp/x dbname=y", True), ("dbname=y user=z", True),
    ("postgresql://u@10.0.0.5/db", False), ("postgresql://u@localhost,10.0.0.5/db", False), ("postgresql://u@/db?host=10.0.0.5", False),
    ("postgresql://u@localhost/db?hostaddr=10.0.0.5", False), ("host=10.0.0.5 dbname=y", False), ("hostaddr=10.0.0.5 dbname=y", False),
])
def test_rehearse_hosts_local_only(dsn, ok):
    """S9：副本演练与 --with-bootstrap 只连 Unix socket 或回环"""
    if ok:
        M.local_only_hosts(dsn, set())
    else:
        with pytest.raises(M.Refused):
            M.local_only_hosts(dsn, set())
    M.local_only_hosts("postgresql://u@10.0.0.5/db", {"10.0.0.5"})  # 显式放行


def test_rehearse_remote_refused_before_connecting(monkeypatch, capsys):
    monkeypatch.setenv("MIGRATE_DSN", "postgresql://u@10.0.0.5/db")
    assert M.main(["rehearse", "--target-label", "x-replica", "--replica", "--confirm", "x"]) == 2
    assert "Unix socket 或回环" in capsys.readouterr().err


def test_no_builtin_connection_strings():
    text = (M.ROOT / "deploy/migrate/migrate.py").read_text(encoding="utf-8")
    import re
    assert not re.search(r"postgres(ql)?://[^\"'\s]*@", text), "执行器源码里不能有连接串"
