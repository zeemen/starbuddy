"""一致性测试的变异测试（spec 范围第 4 条）：参考假适配器逐一注入 FAULTS 里的违约行为，
断言一致性测试每次都失败；不注入时必须通过。证明测试确实在守这些规则，而不是恰好通过。

退出码：0 全部发现；1 有漏网或基线不通过；3 缺依赖。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

try:
    import pytest  # noqa: F401
except ImportError:  # pragma: no cover
    print("conformance-mutation: 跳过（缺少 pytest；pip install -r packages/contracts/requirements-dev.txt）")
    sys.exit(3)

from reference_adapter import FAULT_CONTRACT, FAULTS  # noqa: E402
from support import SUBJECT_METHODS  # noqa: E402


def run(fault: str | None, asynchronous: bool = False, contract: str | None = None, report: Path | None = None) -> tuple[int, str]:
    """contract：以哪个契约版本跑（v1.1 present_item 的违约要在 1.1.0 下跑；候选版只在测试里打开）。"""
    cmd = [sys.executable, str(HERE / "cli.py"), "--reference", "--quiet"] + (["--fault", fault] if fault else [])
    if asynchronous:
        cmd.append("--reference-async")
    if report:
        cmd += ["--report", str(report)]
    env = dict(os.environ)
    if contract:
        env.update(STARBUDDY_CONTRACT_VERSION=contract, STARBUDDY_ALLOW_CANDIDATE_CONTRACT="1")
    p = subprocess.run(cmd, capture_output=True, text=True, env=env)
    return p.returncode, p.stdout


def report_problems(report: dict) -> list[str]:
    """Gate 0 覆盖率的分母只算六个学科方法（PLT-12 审查阻断 1）：present_item 不能进 of / implemented / passed。"""
    out = []
    if report.get("of") != len(SUBJECT_METHODS) or set(report.get("methods", {})) != set(SUBJECT_METHODS):
        out.append(f"覆盖率分母不是六个学科方法：of={report.get('of')}，methods={sorted(report.get('methods', {}))}")
    if report.get("implemented", 0) > len(SUBJECT_METHODS) or report.get("passed", 0) > len(SUBJECT_METHODS):
        out.append("implemented / passed 超过六个学科方法")
    ro = report.get("read_only_methods", {}).get("present_item", {})
    if not (ro.get("implemented") and ro.get("passed") and ro.get("tests")):
        out.append("read_only_methods.present_item 没有单列，或没有跑过、没有通过")
    return out


def check_report_denominator() -> list[str]:
    """契约 1.1.0 下跑一遍参考假适配器并读覆盖率报告；另用合成输入直接核对报告函数（七个方法都实现且通过时 of 仍是 6）。"""
    from support import coverage_report  # noqa: PLC0415

    caps = {"methods": {m: {"implemented": True} for m in (*SUBJECT_METHODS, "present_item")}}
    synthetic = coverage_report(caps, {m: ["passed"] for m in caps["methods"]}, "aya", 0, [], read_only=("present_item",))
    problems = [f"合成输入：{p}" for p in report_problems(synthetic)]
    if synthetic.get("implemented") != 6 or synthetic.get("passed") != 6:
        problems.append("合成输入：七个方法都实现且通过时，implemented / passed 应为 6")
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "report.json"
        rc, _ = run(None, contract="1.1.0", report=path)
        if rc != 0 or not path.exists():
            return [*problems, f"契约 1.1.0 下参考假适配器没有生成覆盖率报告（退出码 {rc}）"]
        report = json.loads(path.read_text(encoding="utf-8"))
    problems += [f"1.1.0 报告：{p}" for p in report_problems(report)]
    if report.get("implemented") != 2 or report.get("passed") != 2:  # 参考假适配器只实现 grade、diagnose
        problems.append(f"1.1.0 报告：参考假适配器应为 implemented=2、passed=2，实为 {report.get('implemented')}、{report.get('passed')}")
    return problems


def main() -> int:
    denominator = check_report_denominator()
    if denominator:
        print("conformance-mutation: 失败（覆盖率报告的分母不对）")
        for p in denominator:
            print(f"  - {p}")
        return 1
    for contract in (None, *sorted(set(FAULT_CONTRACT.values()))):
        for asynchronous in (False, True):
            base, _ = run(None, asynchronous, contract)
            if base != 0:
                print(f"conformance-mutation: 失败（不注入违约时一致性测试就不通过，异步={asynchronous}，契约={contract or '缺省'}，退出码 {base}）")
                return 1
    missed = []
    for fault, (desc, expected) in FAULTS.items():
        rc, out = run(fault, contract=FAULT_CONTRACT.get(fault))
        failed = re.findall(r"^FAILED \S*::(\w+)", out, re.M)
        if rc != 1:  # 1 = 有测试失败；其他值说明不是被测试发现的
            missed.append(f"{fault}（{desc}）：退出码 {rc}")
        elif expected not in failed:  # 必须由指定的测试发现，而不是碰巧被别的测试抓到
            missed.append(f"{fault}（{desc}）：应由 {expected} 发现，实际失败的是 {sorted(set(failed))}")
    if missed:
        print(f"conformance-mutation: 失败（{len(missed)} 个违约没有被发现）")
        for m in missed:
            print(f"  - {m}")
        return 1
    print(f"conformance-mutation: 通过（{len(FAULTS)} 类违约全部被发现）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
