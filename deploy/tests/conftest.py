"""deploy/ 的测试（LT-01）：静态校验、开关矩阵、环境变量清单、迁移执行器单测。只读仓库文件，不连库、不联网。
用学习服务的虚拟环境运行（需要 PyYAML 与 pytest）：services/learning/.venv/bin/python -m pytest deploy/tests"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
sys.path.insert(0, str(DEPLOY / "migrate"))


def load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def flags():
    return load_yaml(DEPLOY / "flags" / "staging.yaml")


@pytest.fixture(scope="session")
def inventory():
    return load_yaml(DEPLOY / "config" / "env-inventory.yaml")


@pytest.fixture(scope="session")
def compose_staging():
    return load_yaml(DEPLOY / "compose" / "staging.yaml")


@pytest.fixture(scope="session")
def compose_local():
    return load_yaml(DEPLOY / "compose" / "local-test.yaml")


@pytest.fixture(scope="session")
def k8s_docs():
    return [d for d in yaml.safe_load_all((DEPLOY / "k8s" / "staging.yaml").read_text(encoding="utf-8")) if d]


def make_check_targets() -> set[str]:
    """make check 实际包含的全部目标：check 的依赖里的 $(VAR) 与 check-core 都按 Makefile 展开（check-core 由 CHECK_GROUP_* 组成）。"""
    import re

    make = (ROOT / "Makefile").read_text(encoding="utf-8")
    variables = dict(re.findall(r"^([A-Z0-9_]+) := (.*)$", make, re.M))

    def deps(target: str) -> list[str]:
        m = re.search(rf"^{re.escape(target)}:(.*)$", make, re.M)
        return m.group(1).split() if m else []

    out: set[str] = set()
    todo = deps("check")
    while todo:
        t = todo.pop()
        v = re.fullmatch(r"\$\(([A-Z0-9_]+)\)", t)
        if v:
            todo.extend(variables[v.group(1)].split())
        elif t == "check-core":
            todo.extend(deps("check-core"))
        else:
            out.add(t)
    return out
