"""一致性测试的变异测试（spec 范围第 4 条）：参考假适配器逐一注入 FAULTS 里的违约行为，
断言一致性测试每次都失败；不注入时必须通过。证明测试确实在守这些规则，而不是恰好通过。

退出码：0 全部发现；1 有漏网或基线不通过；3 缺依赖。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

try:
    import pytest  # noqa: F401
except ImportError:  # pragma: no cover
    print("conformance-mutation: 跳过（缺少 pytest；pip install -r packages/contracts/requirements-dev.txt）")
    sys.exit(3)

from reference_adapter import FAULTS  # noqa: E402


def run(fault: str | None, asynchronous: bool = False) -> tuple[int, str]:
    cmd = [sys.executable, str(HERE / "cli.py"), "--reference", "--quiet"] + (["--fault", fault] if fault else [])
    if asynchronous:
        cmd.append("--reference-async")
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout


def main() -> int:
    for asynchronous in (False, True):
        base, _ = run(None, asynchronous)
        if base != 0:
            print(f"conformance-mutation: 失败（不注入违约时一致性测试就不通过，异步={asynchronous}，退出码 {base}）")
            return 1
    missed = []
    for fault, (desc, expected) in FAULTS.items():
        rc, out = run(fault)
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
