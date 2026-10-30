"""LT-03 演练与回滚手册（deploy/runbooks）的静态核对：手册齐全、写明由人执行与两人复核；引用的迁移执行器子命令与参数真实存在、
确认串格式与执行器一致；连库一律 verify-full；只有占位、没有真实地址与凭据；演练对象是副本（-replica 标签 + --replica-host-allow）。"""
from __future__ import annotations

import re

import pytest

from conftest import DEPLOY

RB = DEPLOY / "runbooks"
MIGRATE = (DEPLOY / "migrate" / "migrate.py").read_text(encoding="utf-8")
DOCS = ["README.md", "rds-restore-and-migrate-rehearsal.md", "rollback.md", "weekly-restore-verify.md", "staging-first-deploy.md"]


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


# ---------------------------------------------------------------- staging 首次部署与走查手册（LT-01 至 LT-05 串联）
# 手册里的命令、Makefile 目标、脚本路径、变量名、Terraform 资源地址、k8s 名字、指标与告警名都必须在仓库里真实存在；
# 改了脚本 / IaC / 清单而没改手册，这里会失败。
FIRST = "staging-first-deploy.md"
IAC = DEPLOY / "iac" / "alicloud"
K8S = (DEPLOY / "k8s" / "staging.yaml").read_text(encoding="utf-8")
ROOT = DEPLOY.parent
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")
TF = {p.name: p.read_text(encoding="utf-8") for p in sorted(IAC.glob("*.tf"))}

# 不在仓库里定义、由外部工具约定的变量名（逐个写明来源）
EXTERNAL_VARS = {
    "ALICLOUD_ACCESS_KEY", "ALICLOUD_SECRET_KEY", "ALICLOUD_SECURITY_TOKEN",  # aliyun/alicloud provider 与 Terraform OSS 后端
    "ALIBABA_CLOUD_ACCESS_KEY_ID", "ALIBABA_CLOUD_ACCESS_KEY_SECRET", "ALIBABA_CLOUD_SECURITY_TOKEN",  # 同上（新名字）
    "TF_DATA_DIR", "TF_INPUT",  # Terraform 命令行
    "COSIGN_PASSWORD",  # cosign 约定的私钥口令变量
}


def first() -> str:
    return text(FIRST)


def code_lines(t: str) -> list[str]:
    out, inside = [], False
    for line in t.splitlines():
        if line.strip().startswith("```"):
            inside = not inside
            continue
        if inside:
            out.append(line.strip())
    return out


def tf_resources() -> set[str]:
    return {f"{a}.{b}" for src in TF.values() for a, b in re.findall(r'^resource "(\w+)" "(\w+)"', src, re.M)}


def tf_variables() -> dict[str, str]:
    src = TF["variables.tf"]
    return {m.group(1): m.group(2) for m in re.finditer(r'^variable "(\w+)" \{(.*?)^\}', src, re.M | re.S)}


def test_first_deploy_sections_and_order():
    t = first()
    pos = [t.index(f"\n## {i}. ") for i in range(8)]
    assert pos == sorted(pos), "第 0–7 节齐全且按顺序"
    phases = ["**阶段 A：网络**", "**阶段 B：RDS / OSS / ACR / SLS**", "**阶段 C：ACK**", "**阶段 D：防火墙与 NAT**"]
    assert [t.index(p) for p in phases] == sorted(t.index(p) for p in phases)
    steps = ["1. **库连接核对**", "2. **学习服务**", "3. **知识服务、洞察服务**", "4. **调度器**", "5. **BFF**", "6. **前端、审核台后端**"]
    assert [t.index(s) for s in steps] == sorted(t.index(s) for s in steps)
    # 0058 先于学习服务：主实例迁移在部署学习服务之前
    assert t.index("migrate.py up --dsn-env PRIMARY_DSN") < t.index("kubectl apply -f <部署目录>/10-learning.yaml")
    assert t.index("rehearse --dsn-env REPLICA_DSN") < t.index("migrate.py up --dsn-env PRIMARY_DSN"), "副本演练先于主实例"


def test_first_deploy_follows_lt03_decision_7():
    t = first()
    for must in ("逐次确认", "不执行 destroy", "OSS 远程后端", "锁表", "不落仓库", "Claude 不读取、不打印其值"):
        assert must in t, must
    # 计划审阅清单（必须列出的五项）
    for must in ("新增 / 修改 / 删除数量", "包年包月或按量计费资源", "安全组与出网规则", "公网暴露检查", "加密与 RAM 变更"):
        assert must in t, must
    lines = code_lines(t)
    assert not any("destroy" in ln for ln in lines), "命令里不出现 destroy"
    tf_lines = [ln for ln in lines if ln.startswith("terraform ")]
    assert tf_lines and all(ln.startswith("terraform -chdir=deploy/iac/alicloud ") for ln in tf_lines)
    plans = [ln for ln in tf_lines if " plan " in ln]
    applies = [ln for ln in tf_lines if " apply " in ln]
    assert plans and all(re.search(r" -out=<计划目录>/\S+\.tfplan", ln) for ln in plans), "plan 一律 -out 到仓库之外的计划目录"
    assert applies and all(re.search(r"<计划目录>/\S+\.tfplan$", ln) and "auto-approve" not in ln for ln in applies), "apply 只用已审阅的计划文件"
    inits = [ln for ln in tf_lines if " init " in ln]
    assert inits and all("-backend-config=<计划目录>/backend.hcl" in ln for ln in inits)


def test_first_deploy_terraform_matches_iac():
    t = first()
    # 版本约束与 versions.tf 一致
    assert '">= 1.6.0, < 2.0.0"' in TF["versions.tf"] and ">= 1.6.0, < 2.0.0" in t
    assert '"~> 1.230"' in TF["versions.tf"] and "~> 1.230" in t
    assert 'backend "oss" {}' in TF["versions.tf"]
    # -backend-config 参数清单：OSS 后端的参数名
    section = t[t.index("### 1.2"):t.index("### 1.3")]
    keys = set(re.findall(r"^\| `([a-z_]+)` \|", section, re.M))
    assert keys == {"bucket", "prefix", "key", "region", "tablestore_endpoint", "tablestore_table", "encrypt", "acl"}, keys
    # 分阶段的 -target 都是真实资源，且四个阶段合起来恰好覆盖全部资源（每个资源只出现在一个阶段）
    targets = re.findall(r"-target=(\S+)", t[t.index("### 1.6"):t.index("\n## 2. ")])
    assert len(targets) == len(set(targets)), "同一资源只在一个阶段创建"
    assert set(targets) == tf_resources(), sorted(set(targets) ^ tf_resources())
    # 必填变量（没有缺省值的）与 0.6 第 4 条一致
    v = tf_variables()
    required = {n for n, body in v.items() if not re.search(r"^\s*default\s*=", body, re.M)}
    item = re.search(r"4\. `terraform\.tfvars` 必填项(.*?)\n\n", t, re.S).group(1)
    assert set(re.findall(r"`([a-z0-9_]+)`", item)) == required, required
    # 输出名真实存在
    outputs = set(re.findall(r'^output "(\w+)"', TF["outputs.tf"], re.M))
    for o in ("slb_acl_id", "log_project"):
        assert f"`{o}`" in t and o in outputs, o
    assert "rds_private_host" in outputs
    # 确认变量：bool、无缺省值、不再有变量级 == true 校验（LT-03 决定记录第 8 条；IaC 改了要同步改手册 0.6 第 1 条）
    assert "== true" not in v["nat_firewall_confirmed"] and re.search(r"type\s*=\s*bool", v["nat_firewall_confirmed"])
    assert "-var nat_firewall_confirmed=false" in t and "R11" in t
    # 静态检查命令与脚本一致
    sh = (DEPLOY / "scripts" / "iac-validate.sh").read_text(encoding="utf-8")
    assert "--skip-download" in sh and "IAC_PLUGIN_DIR" in sh and "CHECKPOINT_DISABLE=1" in sh and "TF_IN_AUTOMATION" in sh
    assert "python3 -I deploy/iac/check_iac.py deploy/iac/alicloud" in t
    assert "python3 -I deploy/iac/check_iac.py deploy/iac/alicloud" in (DEPLOY / "scripts" / "deploy-check.sh").read_text(encoding="utf-8")


def test_first_deploy_make_targets_scripts_and_paths_exist():
    t = first()
    targets = set(re.findall(r"^([a-z][a-z0-9-]*):", MAKEFILE, re.M))
    used = re.findall(r"make ([a-z][a-z0-9-]*)((?: [A-Z_]+=\S+)*)", t)
    assert used
    for target, args in used:
        assert target in targets, f"Makefile 没有目标 {target}"
        for var in re.findall(r"([A-Z_]+)=", args):
            assert var in MAKEFILE or var in (DEPLOY / "scripts" / "iac-validate.sh").read_text(encoding="utf-8"), var
    paths = set(re.findall(r"\b((?:deploy|db|evals|services)/[A-Za-z0-9_./*-]+)", t))
    assert len(paths) >= 15
    for p in paths:
        p = p.rstrip(".")
        assert list(ROOT.glob(p)) or (ROOT / p).exists(), f"路径不存在：{p}"
    # 镜像：流水线入口与验签参数与 pipeline.sh 一致；摘要行数与镜像清单一致
    pipe = (DEPLOY / "ci" / "pipeline.sh").read_text(encoding="utf-8")
    assert "bash deploy/ci/pipeline.sh" in t and "bash deploy/ci/pipeline.sh" in (DEPLOY / "ci" / "flow.yaml").read_text(encoding="utf-8")
    assert "cosign verify --insecure-ignore-tlog=true --key" in pipe and "cosign verify --insecure-ignore-tlog=true --key" in t
    assert "verify-attestation --insecure-ignore-tlog=true" in pipe and "--type spdxjson" in pipe and "--type spdxjson" in t
    n = len([ln for ln in (DEPLOY / "ci" / "images.txt").read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")])
    assert f"恰好 {n} 行" in t and f'-eq {n} ]' in pipe
    assert "db/bootstrap/roles.sql" in t and "--with-bootstrap" in t and "--to 0009" in t


def test_first_deploy_env_var_names_exist():
    t = first()
    inv = load_inventory_names()
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in [
        *sorted((DEPLOY / "ci").glob("*")), *sorted((DEPLOY / "scripts").glob("*.sh")), DEPLOY / "migrate" / "migrate.py",
        *[RB / d for d in DOCS if d != FIRST], DEPLOY / "config" / "README.md", *sorted((DEPLOY / "k8s").glob("*.yaml")),
        DEPLOY / "flags" / "staging.yaml", ROOT / "Makefile", ROOT / "services" / "learning" / "learning" / "config.py"] if p.is_file())
    names = set(re.findall(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b", t))
    assert {"PRIMARY_DSN", "REPLICA_DSN", "ACR_REGISTRY", "COSIGN_KEY_REF", "IAC_PLUGIN_DIR", "SMOKE_ENV"} <= names
    for n in names:
        ok = n in EXTERNAL_VARS or re.search(rf"\b{n}\b", corpus) or any(re.fullmatch(p, n) for p in inv)
        assert ok, f"变量名 {n} 在仓库里找不到（也不在外部工具约定的名单里）"
    # 0.5 表里只写名字：没有 NAME=值 的赋值（占位除外）
    section = t[t.index("### 0.5"):t.index("### 0.6")]
    assert not re.search(r"\b[A-Z][A-Z0-9_]+=[^<\s]", section)


def load_inventory_names() -> list[str]:
    import yaml
    inv = yaml.safe_load((DEPLOY / "config" / "env-inventory.yaml").read_text(encoding="utf-8"))
    pats = []
    for svc in inv["services"].values():
        for v in svc["vars"]:
            pats.append(re.sub(r"<[A-Z]+>", "[A-Z]+", re.escape(v["name"]).replace(r"\<", "<").replace(r"\>", ">")))
    return pats


def test_first_deploy_k8s_names_match_manifests():
    t = first()
    deployments = set(re.findall(r"kind: Deployment\nmetadata: \{name: ([a-z-]+),", K8S))
    containers = set(re.findall(r"^\s+- name: ([a-z-]+)\n\s+image: ", K8S, re.M))
    assert deployments == {"learning", "knowledge", "insight", "review-console", "bff", "web", "scheduler"}
    for d in re.findall(r"deployment/([a-z-]+)", t):
        assert d in deployments, d
    for c in re.findall(r" -c ([a-z-]+)", t):
        assert c in containers, c
    for a in re.findall(r"-l app=([a-z-]+)", t):
        assert a in deployments, a
    # Secret 名与键：与清单 secretKeyRef（含注释里待解开的接线行）和 kms.tf 的凭据名单一致
    app_secrets = set(re.findall(r'"(starbuddy-[a-z-]+)"', re.search(r"app_secrets = \[(.*?)\]", TF["kms.tf"], re.S).group(1)))
    table = t[t.index("| Secret | 键 |"):t.index("- 核对（只看名字")]
    rows = {m.group(1): m.group(2) for m in re.finditer(r"^\| `(starbuddy-[a-z-]+)` \| (.*)\|$", table, re.M)}
    assert set(rows) == app_secrets, sorted(set(rows) ^ app_secrets)
    for name, key in re.findall(r"secretKeyRef: \{name: ([a-z-]+), key: ([a-z-]+)\}", K8S):
        assert f"`{key}`" in rows[name], f"{name}/{key}"
    for f in re.findall(r"/run/config/([A-Za-z0-9.-]+)", K8S):
        assert f"`{f}`" in rows["starbuddy-signed-config"], f
    assert "/run/otp/otp.json" in K8S and "`otp.json`" in rows["starbuddy-staging-otp"]
    for name, key in re.findall(r"configMapKeyRef: \{name: ([a-z-]+), key: ([a-z-]+)\}", K8S):
        assert f"`{name}`" in t and f"`{key}`" in t, f"{name}/{key}"
    assert "configMap: {name: starbuddy-rds-ca}" in K8S and "`starbuddy-rds-ca`" in t and "`ca.pem`" in t
    assert "-o yaml` / `-o json`" in t and not re.search(r"get secret\S* .*-o (yaml|json)", "\n".join(code_lines(t))), "不读 Secret 取值"
    # 服务间 readyz：clusterIP 与端口来自清单，覆盖全部有 clusterIP 的 Service
    svc = set(re.findall(r"clusterIP: (10\.96\.100\.\d+), ports: \[\{port: (\d+),", K8S))
    urls = set(re.findall(r"http://(10\.96\.100\.\d+):(\d+)/readyz", t))
    assert urls == svc, sorted(urls ^ svc)
    # 只用仓库里出现过的网段 / 地址（不写真实地址）
    ip_corpus = K8S + "\n".join(TF.values()) + (DEPLOY / "config" / "README.md").read_text(encoding="utf-8")
    for ip in set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}(?:/\d+)?\b", t)):
        assert ip in ip_corpus, ip


def test_first_deploy_flags_metrics_alerts_and_roles_exist(flags):
    t = first()
    # 开关矩阵：每个 pending 项都在第 5 节表里，并列出依赖它的每个 id；保持关的 id 都写明
    section = t[t.index("\n## 5. "):t.index("\n## 6. ")]
    items = [*flags["subjects"], *flags["features"]]
    pending: dict[str, set[str]] = {}
    for it in items:
        for p in it.get("pending", []):
            pending.setdefault(p, set()).add(it["id"])
    assert set(pending) == {"migration_0058", "migration_0059", "migration_0060", "signed_content", "model_provider", "human"}
    for p, ids in pending.items():
        row = re.search(rf"^\| `{p}` \|([^\n]*)$", section, re.M)
        assert row, p
        cell = row.group(1).split("|")[0]
        assert set(re.findall(r"`([a-z_0-9]+)`", cell)) == ids, (p, ids)
    for it in items:
        if it["state"] == "off" and not it.get("pending"):
            assert f"`{it['id']}`" in section, it["id"]
    # 指标名在登记清单里；数据库角色 / schema 名在 bootstrap 与迁移里
    import yaml
    inv = yaml.safe_load((DEPLOY / "observability" / "metrics-inventory.yaml").read_text(encoding="utf-8"))
    metrics = {m for ms in inv["services"].values() for m in ms}
    roles = (ROOT / "db" / "bootstrap" / "roles.sql").read_text(encoding="utf-8")
    m0009 = next((ROOT / "db" / "migrations").glob("0009_*.up.sql")).read_text(encoding="utf-8")
    for name in set(re.findall(r"\bstarbuddy_[a-z_]+\b", t)):
        assert name in metrics or f"CREATE ROLE {name} " in roles or name in m0009, name
    alerts = (DEPLOY / "observability" / "alerts.yaml").read_text(encoding="utf-8")
    for a in set(re.findall(r"\bStarbuddy[A-Za-z0-9]+\b", t)):
        assert f"alert: {a}\n" in alerts, a
    # 0058 的函数签名与迁移一致
    m0058 = next((ROOT / "db" / "migrations").glob("0058_*.up.sql")).read_text(encoding="utf-8")
    assert "platform.learning_meter_check(uuid, text, uuid, text)" in m0058 and "platform.learning_meter_check(uuid, text, uuid, text)" in t
