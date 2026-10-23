"""LT-03 演练与回滚手册（deploy/runbooks）的静态核对：手册齐全、写明由人执行与两人复核；引用的迁移执行器子命令与参数真实存在、
确认串格式与执行器一致；连库一律 verify-full；只有占位、没有真实地址与凭据；演练对象是副本（-replica 标签 + --replica-host-allow）。"""
from __future__ import annotations

import re

import pytest

from conftest import DEPLOY

RB = DEPLOY / "runbooks"
MIGRATE = (DEPLOY / "migrate" / "migrate.py").read_text(encoding="utf-8")
DOCS = ["README.md", "rds-restore-and-migrate-rehearsal.md", "rollback.md"]


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


def test_rollback_covers_app_migration_pitr_and_iac():
    t = text("rollback.md")
    for must in ("kubectl -n starbuddy-staging set image", "@sha256:", "migrate.py down", "按时间点恢复", "sslmode=verify-full",
                 "删除保护", "Recreate", "/readyz"):
        assert must in t, must
