#!/usr/bin/env python3
"""攻击面清单与测试的对应检查（specs/household-pentest-sprint3/spec.md 第 7 节“清单与测试的对应”）。

make check-fast / make attack-surface-check 调用。只用标准库，约 0.1 秒。核对：
1. docs/security/attack-surface.md 的条目编号与 spec 第 2 节一一对应（不多、不少、不重复）；
2. “已有自动化断言”一列里的每条引用都真实存在：
   - `ref:路径#片段`：文件存在且含这段文字（表格里的 \\| 还原成 |）；
   - `mut:名称`：db/tests/mutations.sh 里有这个变异；`mut-bff:名称`：services/bff/tests/mutations.sh 里有；
3. 没有引用的条目，这一列写“无”，而且“渗透测试要做的”一列标了“人工”或“第二轮”；每条都要标这两者之一；
4. docs/security/pentest/scope.md 的 SECURITY DEFINER 函数表与 db/tests/expected/privileges.txt 一致：
   函数清单对 secdef| 行，“可执行的应用角色”一列对 func| 行；
5. 交接包文件齐全，findings.csv 表头与 spec 第 6 节一致。
退出码：0 通过；1 失败。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "specs/household-pentest-sprint3/spec.md"
SURFACE = ROOT / "docs/security/attack-surface.md"
SCOPE = ROOT / "docs/security/pentest/scope.md"
PRIVILEGES = ROOT / "db/tests/expected/privileges.txt"
MUTATION_FILES = {"mut": ROOT / "db/tests/mutations.sh", "mut-bff": ROOT / "services/bff/tests/mutations.sh"}
HANDOFF = ["scope.md", "rules-of-engagement.md", "report-template.md", "findings.csv", "coverage.md"]
FINDINGS_HEADER = "finding_id,severity,surface_ids,status,fix_pr,regression_test"
COLUMNS = ["编号", "攻击面", "攻击者 / 前提", "现有防护", "已有自动化断言", "渗透测试要做的", "已知遗留"]


def spec_ids(text: str) -> list[str]:
    """spec 第 2 节里以“- A1 ”开头的条目编号"""
    m = re.search(r"^### 2\..*?(?=^### 3\.)", text, re.S | re.M)
    if not m:
        raise SystemExit("attack-surface-check: 失败（spec 里找不到第 2 节）")
    return re.findall(r"^- ([A-Z]\d+) ", m.group(0), re.M)


def split_row(line: str) -> list[str]:
    cells = re.split(r"(?<!\\)\|", line.strip())
    return [c.strip() for c in cells[1:-1]]


def mutation_names(path: Path) -> set[str]:
    return set(re.findall(r'^\s*\["(.+?)"\]=', path.read_text(encoding="utf-8"), re.M))


def check_refs(cell: str, muts: dict[str, set[str]], errors: list[str], sid: str) -> int:
    n = 0
    for span in re.findall(r"`([^`]+)`", cell):
        prefix, sep, rest = span.partition(":")
        if not sep or prefix not in ("ref", *muts):
            continue
        n += 1
        if prefix == "ref":
            path, hash_, fragment = rest.partition("#")
            fragment = fragment.replace("\\|", "|")
            target = ROOT / path
            if not hash_ or not fragment:
                errors.append(f"{sid}：引用缺少 #片段：{span}")
            elif not target.is_file():
                errors.append(f"{sid}：引用的文件不存在：{path}")
            elif fragment not in target.read_text(encoding="utf-8"):
                errors.append(f"{sid}：{path} 里找不到“{fragment}”")
        elif rest not in muts[prefix]:
            errors.append(f"{sid}：{MUTATION_FILES[prefix].relative_to(ROOT)} 里没有变异“{rest}”")
    return n


def main() -> int:
    errors: list[str] = []
    expected = spec_ids(SPEC.read_text(encoding="utf-8"))
    muts = {k: mutation_names(p) for k, p in MUTATION_FILES.items()}

    rows: dict[str, list[str]] = {}
    for line in SURFACE.read_text(encoding="utf-8").splitlines():
        if line.startswith("| 编号 "):
            if split_row(line) != COLUMNS:
                errors.append(f"表头不是 {' | '.join(COLUMNS)}：{line}")
            continue
        m = re.match(r"^\| ([A-Z]\d+) \|", line)
        if not m:
            continue
        sid = m.group(1)
        if sid in rows:
            errors.append(f"{sid}：重复出现")
        cells = split_row(line)
        if len(cells) != len(COLUMNS):
            errors.append(f"{sid}：应有 {len(COLUMNS)} 列，实际 {len(cells)} 列（单元格里的 | 要写成 \\|）")
            continue
        rows[sid] = cells

    missing = [s for s in expected if s not in rows]
    extra = [s for s in rows if s not in expected]
    if missing:
        errors.append(f"清单缺少 spec 里的条目：{'、'.join(missing)}")
    if extra:
        errors.append(f"清单有 spec 里没有的条目：{'、'.join(extra)}")

    n_refs = 0
    for sid, cells in rows.items():
        auto, todo = cells[4], cells[5]
        n = check_refs(auto, muts, errors, sid)
        n_refs += n
        if n == 0 and auto != "无":
            errors.append(f"{sid}：没有可核对的引用时，“已有自动化断言”一列只能写“无”（实际：{auto}）")
        if "人工" not in todo and "第二轮" not in todo:
            errors.append(f"{sid}：“渗透测试要做的”一列必须标“人工”或“第二轮”")

    # scope.md 的函数表 ↔ 授权快照
    snapshot = {l[len("secdef|"):] for l in PRIVILEGES.read_text(encoding="utf-8").splitlines() if l.startswith("secdef|")}
    scope = SCOPE.read_text(encoding="utf-8") if SCOPE.is_file() else ""
    table = re.search(r"<!-- secdef-table:start -->(.*?)<!-- secdef-table:end -->", scope, re.S)
    if not table:
        errors.append("scope.md 里找不到 SECURITY DEFINER 函数表（secdef-table 标记）")
    else:
        rows_ = re.findall(r"^\| `(platform\.[a-z_]+\([^`]*\))` \| [^|]+ \| ([^|]+) \|", table.group(1), re.M)
        listed = [f for f, _ in rows_]
        # “可执行的应用角色”一列与快照的 func| 行一致（属主 authz 与超级用户不算）
        granted: dict[str, set[str]] = {}
        for line in PRIVILEGES.read_text(encoding="utf-8").splitlines():
            parts = line.split("|")
            if parts[0] == "func" and parts[3] == "EXECUTE" and parts[2] not in ("starbuddy_authz", "SUPERUSER"):
                granted.setdefault(parts[1], set()).add(parts[2])
        for f, who in rows_:
            want = set() if who.strip().startswith("无") else {
                "starbuddy_" + w.strip() for w in re.split(r"[、,，]", who) if w.strip()}
            if granted.get(f, set()) != want:
                errors.append(f"scope.md 函数表 {f} 的可执行角色写的是“{who.strip()}”，授权快照里是 "
                              f"{'、'.join(sorted(granted.get(f, set()))) or '无'}")
        if len(listed) != len(set(listed)):
            errors.append("scope.md 函数表有重复行")
        for f in sorted(snapshot - set(listed)):
            errors.append(f"scope.md 函数表缺少 {f}（授权快照里是 SECURITY DEFINER）")
        for f in sorted(set(listed) - snapshot):
            errors.append(f"scope.md 函数表多了 {f}（授权快照里不是 SECURITY DEFINER 或不存在）")
        m = re.search(r"### 3\.2 SECURITY DEFINER 函数（(\d+) 个）", scope)
        if not m or int(m.group(1)) != len(snapshot):
            errors.append(f"scope.md 3.2 节标题里的函数个数应为 {len(snapshot)}")

    for name in HANDOFF:
        if not (ROOT / "docs/security/pentest" / name).is_file():
            errors.append(f"交接包缺少 docs/security/pentest/{name}")
    findings = ROOT / "docs/security/pentest/findings.csv"
    if findings.is_file() and findings.read_text(encoding="utf-8").splitlines()[:1] != [FINDINGS_HEADER]:
        errors.append(f"findings.csv 第一行应为 {FINDINGS_HEADER}")

    if errors:
        for e in errors:
            print(f"attack-surface-check: {e}")
        print(f"attack-surface-check: 失败（{len(errors)} 处）")
        return 1
    print(f"attack-surface-check: 通过（{len(rows)} 条攻击面、{n_refs} 条引用全部有效；"
          f"scope.md 的 {len(snapshot)} 个 SECURITY DEFINER 函数与授权快照一致）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
