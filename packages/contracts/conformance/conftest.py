"""一致性测试的 pytest 配置：命令行选项、会话级夹具、覆盖率报告。

用法（一般经 cli.py 调用）：
  pytest packages/contracts/conformance --adapter-url http://127.0.0.1:8787 \\
         --conformance-fixtures adapters/aya/conformance/fixtures.yaml --conformance-report /tmp/aya-conformance.json
  服务间令牌从环境变量 CONFORMANCE_ADAPTER_TOKEN 读取（不放在命令行上）。
"""
from __future__ import annotations

import json
import os
from collections import defaultdict

import pytest

from support import METHODS, AdapterClient, load_fixtures

_results: dict[str, list[str]] = defaultdict(list)
_untagged_failures: list[str] = []


def pytest_addoption(parser):
    g = parser.getgroup("conformance")
    g.addoption("--adapter-url", required=True, help="适配器地址（不含 /plugin/v0）")
    g.addoption("--conformance-fixtures", required=True, help="学科夹具 YAML")
    g.addoption("--conformance-report", default=None, help="覆盖率报告 JSON 输出路径")


def pytest_generate_tests(metafunc):
    """按夹具展开参数：case（每条夹具）、method_case（每个方法的第一条夹具）。"""
    fx = load_fixtures(metafunc.config.getoption("--conformance-fixtures"))
    if "case" in metafunc.fixturenames:
        metafunc.parametrize("case", fx.cases, ids=[c["id"] for c in fx.cases])
    if "method_case" in metafunc.fixturenames:
        firsts = [fx.cases_for(m)[0] for m in METHODS if fx.cases_for(m)]
        metafunc.parametrize("method_case", firsts, ids=[c["method"] for c in firsts])
    if "method" in metafunc.fixturenames:
        metafunc.parametrize("method", METHODS)


@pytest.fixture(scope="session")
def fx(pytestconfig):
    return load_fixtures(pytestconfig.getoption("--conformance-fixtures"))


@pytest.fixture(scope="session")
def client(pytestconfig):
    credential = os.environ.get("CONFORMANCE_ADAPTER_TOKEN", "")
    if len(credential) < 16:
        pytest.exit("缺少 CONFORMANCE_ADAPTER_TOKEN", returncode=2)
    return AdapterClient(pytestconfig.getoption("--adapter-url"), credential)


@pytest.fixture(scope="session")
def capabilities(client):
    r = client.call("GET", "capabilities")
    assert r.status == 200, f"capabilities 返回 {r.status}"
    return r.body


def pytest_runtest_logreport(report):
    tagged = [v for k, v in report.user_properties if k == "method"]
    if report.failed and not tagged:
        _untagged_failures.append(report.nodeid)  # 能力声明、令牌等全局用例，或 setup 阶段的失败
    if report.when != "call":
        return
    for v in tagged:
        _results[v].append(report.outcome)


def pytest_sessionfinish(session, exitstatus):
    path = session.config.getoption("--conformance-report")
    if not path:
        return
    try:
        fx = load_fixtures(session.config.getoption("--conformance-fixtures"))
        client = AdapterClient(session.config.getoption("--adapter-url"), os.environ.get("CONFORMANCE_ADAPTER_TOKEN", ""))
        caps = client.call("GET", "capabilities").body or {}
    except Exception:  # noqa: BLE001 — 报告尽力而为
        caps, fx = {}, None
    # 任何全局用例失败（例如不校验令牌、契约版本不对），或整套测试没有通过，所有方法都不算“通过”（契约守护 S1）
    suite_ok = int(exitstatus) == 0 and not _untagged_failures
    methods = {}
    for m in METHODS:
        implemented = bool(((caps.get("methods") or {}).get(m) or {}).get("implemented"))
        outcomes = _results.get(m, [])
        methods[m] = {
            "implemented": implemented,
            "tests": len(outcomes),
            "failed": sum(o != "passed" for o in outcomes),
            "passed": suite_ok and implemented and bool(outcomes) and all(o == "passed" for o in outcomes),
        }
    report = {
        "subject": fx.subject if fx else None,
        "adapter_version": caps.get("adapter_version"),
        "contract_version": caps.get("contract_version"),
        "implemented": sum(v["implemented"] for v in methods.values()),
        "passed": sum(v["passed"] for v in methods.values()),
        "of": len(METHODS),
        "methods": methods,
        "suite_exit_status": int(exitstatus),
        "global_failures": _untagged_failures,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
