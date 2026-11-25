"""Python 依赖的已知漏洞核对（specs/py-audit-fixes；W13 技术债 T53，每月或升级依赖前手动跑）。

读仓库里的 Python 锁文件（pip-compile 带哈希格式，`名字==版本 \\` 开头的行），逐个查 PyPI 的 JSON 接口
（https://pypi.org/pypi/<名字>/<版本>/json 的 vulnerabilities 字段，数据来自 OSV / GitHub 通告库），列出没有撤回的通告。
只用标准库，不装 pip-audit；要联网，结果随通告库变化，所以不进 CI 卡口。走代理时按环境的 CA 包设 SSL_CERT_FILE。

  python3 tools/dep-audit/pypi_vulns.py              核对全部锁文件；有通告时退出码 1，查询失败退出码 2
  python3 tools/dep-audit/pypi_vulns.py --self-test  解析自检（不联网）
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCK_GLOBS = ["packages/*/requirements*.lock", "services/*/requirements*.lock", "services/*/build-requirements.lock"]
PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==([^\s;\\]+)")


def lock_files() -> list[Path]:
    return sorted({p for g in LOCK_GLOBS for p in ROOT.glob(g)})


def parse(text: str) -> list[tuple[str, str]]:
    """锁文件 → [(规范化包名, 版本)]；注释、哈希行、-e 本地包都跳过"""
    out = []
    for line in text.splitlines():
        m = PIN.match(line)
        if m:
            out.append((re.sub(r"[-_.]+", "-", m.group(1)).lower(), m.group(2)))
    return out


def advisories(name: str, version: str) -> list[dict]:
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30) as r:
        data = json.load(r)
    return [v for v in data.get("vulnerabilities") or [] if not v.get("withdrawn")]


def self_test() -> None:
    sample = "\n".join([
        "# 注释 foo==1.0", "-e ../../packages/contracts/python", "Certifi==2026.7.22 \\", "    --hash=sha256:00 \\",
        "uvicorn[standard]==0.30.0 \\", "typing_extensions==4.12.2 ; python_version < \"3.12\"", "    # via fastapi",
    ])
    got = parse(sample)
    want = [("certifi", "2026.7.22"), ("uvicorn", "0.30.0"), ("typing-extensions", "4.12.2")]
    assert got == want, got
    assert lock_files(), "仓库里应至少有一个 Python 锁文件"
    print(f"pypi_vulns 自检通过（解析 {len(want)} 个正例，跳过注释、哈希、-e 行；找到 {len(lock_files())} 个锁文件）")


def main() -> int:
    pins: dict[tuple[str, str], set[str]] = {}
    for f in lock_files():
        for pin in parse(f.read_text(encoding="utf-8")):
            pins.setdefault(pin, set()).add(str(f.relative_to(ROOT)))
    found, errors = 0, 0
    for (name, version), files in sorted(pins.items()):
        try:
            vulns = advisories(name, version)
        except Exception as e:  # 网络或 PyPI 出错：记下来，最后以退出码 2 结束，不当作“没有漏洞”
            errors += 1
            print(f"查询失败 {name}=={version}: {type(e).__name__}", file=sys.stderr)
            continue
        for v in vulns:
            found += 1
            aliases = ",".join(v.get("aliases") or [])
            print(f"{name}=={version}  {v.get('id')} [{aliases}]  修复版本 {','.join(v.get('fixed_in') or []) or '无'}  出现在 {', '.join(sorted(files))}")
    print(f"pypi_vulns: {len(pins)} 个包与版本，{found} 条未撤回的通告，{errors} 个查询失败")
    return 2 if errors else (1 if found else 0)


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        self_test()
    else:
        sys.exit(main())
