"""LT-03 一致性（契约审查建议 5、决定记录（续）第 6 条）：

- 学习、洞察、知识三个服务的健康端点各带一份（不跨服务导入），代码部分（标记行之后）必须逐字相同；
- staging 连库 TLS 的核对（db_tls_ok）在学习（learning/netpolicy.py，审核台与调度器共用）、洞察、知识三处行为一致，
  固定 CA 路径相同，BFF（src/http/config.ts）与部署编排挂载的 CA 路径也是同一个。
不导入各服务的包（deploy 测试的解释器里没有装它们）：从源码里取出函数，放进只含 os 与 RDS_CA_PATH 的命名空间里执行。全部合成值。"""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HEALTH = [ROOT / "services/learning/learning/health.py", ROOT / "services/insight/insight/service/health.py",
          ROOT / "services/knowledge/knowledge/service/health.py"]
MARK = "# ---- 以下在 learning/learning/health.py、insight/insight/service/health.py、knowledge/knowledge/service/health.py 三处逐字相同"
TLS_SOURCES = [ROOT / "services/learning/learning/netpolicy.py", ROOT / "services/insight/insight/service/config.py",
               ROOT / "services/knowledge/knowledge/service/config.py"]
CA = "/run/rds-ca/ca.pem"


def shared_body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    assert text.count(MARK) == 1, f"{path.relative_to(ROOT)} 缺少共用代码的标记行"
    return text[text.index(MARK):]


def test_three_health_modules_identical():
    bodies = [shared_body(p) for p in HEALTH]
    assert bodies[0] == bodies[1] == bodies[2], "三份 health.py 的代码部分必须逐字相同（改一处同步另外两处）"
    body = bodies[0]
    for needed in ("class ReadyCheck", "READY_CACHE = ", "asyncio.shield(self._task)", "asyncio.wait_for(probe(), timeout)",
                   '"cache-control": "no-store"'):
        assert needed in body, needed
    cache = float(re.search(r"^READY_CACHE = ([0-9.]+)", body, re.M).group(1))
    assert 2.0 <= cache <= 5.0, "就绪结果缓存 2–5 秒"


def _tls_fn(path: Path, ca: str):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    consts = [n for n in tree.body if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "RDS_CA_PATH" for t in n.targets)]
    assert len(consts) == 1 and ast.literal_eval(consts[0].value) == CA, f"{path.relative_to(ROOT)}：RDS_CA_PATH 必须是 {CA}"
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "db_tls_ok"]
    assert len(fns) == 1, f"{path.relative_to(ROOT)} 缺 db_tls_ok"
    ns: dict = {"os": os, "RDS_CA_PATH": ca}
    exec(compile(ast.Module(body=fns, type_ignores=[]), str(path), "exec"), ns)  # noqa: S102 —— 仓库内源码
    return ns["db_tls_ok"]


def test_db_tls_ok_same_behaviour_everywhere(tmp_path):
    ca = tmp_path / "ca.pem"
    ca.write_text("synthetic\n", encoding="utf-8")
    fns = [_tls_fn(p, str(ca)) for p in TLS_SOURCES]
    good = f"postgresql://u@pgm-syn.example.invalid:5432/x?sslmode=verify-full&sslrootcert={ca}"
    cases = {
        good: True,
        good.replace("postgresql://", "postgres://"): True,
        "postgresql://u@172.28.0.20/x": False,
        f"postgresql://u@172.28.0.20/x?sslmode=require&sslrootcert={ca}": False,
        f"postgresql://u@172.28.0.20/x?sslmode=verify-ca&sslrootcert={ca}": False,
        "postgresql://u@172.28.0.20/x?sslmode=verify-full&sslrootcert=/etc/other.pem": False,
        "postgresql://u@172.28.0.20/x?sslmode=verify-full": False,
        f"{good}&sslmode=disable": False,
        f"{good}&sslrootcert=/etc/other.pem": False,
        f"mysql://u@172.28.0.20/x?sslmode=verify-full&sslrootcert={ca}": False,
    }
    for url, want in cases.items():
        got = [f(url) for f in fns]
        assert got == [want] * 3, (url, got)
    missing = [_tls_fn(p, str(tmp_path / "missing.pem")) for p in TLS_SOURCES]
    url = f"postgresql://u@172.28.0.20/x?sslmode=verify-full&sslrootcert={tmp_path / 'missing.pem'}"
    assert [f(url) for f in missing] == [False] * 3, "CA 文件不存在一律拒绝"


def test_bff_and_deploy_use_same_ca_path():
    ts = (ROOT / "services/bff/src/http/config.ts").read_text(encoding="utf-8")
    assert f"export const RDS_CA_PATH = '{CA}';" in ts
    k8s = (ROOT / "deploy/k8s/staging.yaml").read_text(encoding="utf-8")
    assert "mountPath: /run/rds-ca" in k8s, "k8s 把 RDS CA 只读挂到固定目录"


@pytest.mark.parametrize("path", HEALTH)
def test_register_uses_cached_ready(path):
    body = shared_body(path)
    assert "cached = ReadyCheck(probe)" in body and "return respond(await cached())" in body
