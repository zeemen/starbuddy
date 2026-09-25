#!/usr/bin/env python3
"""错因跨科映射表检查与渲染。

检查：
1. 七大类 id 与契约 CrossSubjectErrorCategory、数据库枚举 platform.cross_subject_error 完全一致；
2. 每个学科编码符合契约 subject_category 格式，学科内不重复，maps_to 与 alternatives 都是七大类之一；
3. status = disputed 时必须给出 note；
4. 每个大类至少有两科映射（完整方案第四章：跨科指标须有至少两科的明确证据），不足时报警告；
5. docs/mapping/error-taxonomy.md 里生成的表格与 YAML 一致（--write 重新生成）。

缺 PyYAML 时退出码 3（由 Makefile 决定是否算失败）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
YAML_PATH = ROOT / "docs/mapping/error-taxonomy.v0.9.yaml"
MD_PATH = ROOT / "docs/mapping/error-taxonomy.md"
CONTRACT = ROOT / "packages/contracts/openapi/plugin-api.v0.9.yaml"
DB_ENUM_FILE = ROOT / "db/migrations/0001_platform__foundation.up.sql"
CODE_RE = re.compile(r"^[a-z0-9_\-]{1,48}$")
BEGIN, END = "<!-- BEGIN GENERATED: mapping -->", "<!-- END GENERATED: mapping -->"

try:
    import yaml
except ImportError:  # pragma: no cover
    print("taxonomy-check: 跳过（缺少 PyYAML；pip install -r packages/contracts/requirements-dev.txt）")
    sys.exit(3)


def db_enum() -> list[str]:
    sql = DB_ENUM_FILE.read_text(encoding="utf-8")
    m = re.search(r"CREATE TYPE platform\.cross_subject_error AS ENUM \((.*?)\);", sql, re.S)
    return re.findall(r"'([a-z_]+)'", m.group(1)) if m else []


def contract_enum() -> list[str]:
    doc = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    return doc["components"]["schemas"]["CrossSubjectErrorCategory"]["enum"]


def render(data: dict) -> str:
    labels = {c["id"]: c["label"] for c in data["categories"]}
    lines = ["| 学科 | 分组 | 学科编码 | 原名称 | 映射到 | 状态 | 备选 / 说明 |", "|---|---|---|---|---|---|---|"]
    for subj, s in data["subjects"].items():
        for g in s["groups"]:
            for c in g["codes"]:
                status = "✅ 明确" if c["status"] == "clear" else "⚠️ 待定"
                if c.get("sensitive"):
                    status += "（敏感）"
                extra = []
                if c.get("alternatives"):
                    extra.append("备选：" + "、".join(labels[a] for a in c["alternatives"]))
                if c.get("tags"):
                    extra.append("下辖标签：" + "、".join(f"`{t}`" for t in c["tags"]))
                if c.get("note"):
                    extra.append(c["note"])
                lines.append(f"| {s['label']} | {g['name']} | `{c['code']}` | {c['label']} | "
                             f"{labels[c['maps_to']]} `{c['maps_to']}` | {status} | {'；'.join(extra)} |")
    lines.append("")
    lines.append("| 大类 | 映射到它的学科 | 编码数 |")
    lines.append("|---|---|---|")
    for cat in data["categories"]:
        subs = [s["label"] for subj, s in data["subjects"].items()
                if any(c["maps_to"] == cat["id"] for g in s["groups"] for c in g["codes"])]
        n = sum(1 for s in data["subjects"].values() for g in s["groups"] for c in g["codes"] if c["maps_to"] == cat["id"])
        lines.append(f"| {cat['label']} `{cat['id']}` | {'、'.join(subs) or '（无）'} | {n} |")
    return "\n".join(lines)


def main() -> int:
    write = "--write" in sys.argv
    data = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))
    errors: list[str] = []
    warnings: list[str] = []

    cats = [c["id"] for c in data["categories"]]
    if cats != contract_enum():
        errors.append(f"七大类与契约 CrossSubjectErrorCategory 不一致：{cats} vs {contract_enum()}")
    if cats != db_enum():
        errors.append(f"七大类与数据库枚举 platform.cross_subject_error 不一致：{cats} vs {db_enum()}")

    total = disputed = 0
    for subj, s in data["subjects"].items():
        seen: set[str] = set()
        for g in s["groups"]:
            if not g.get("source"):
                errors.append(f"{subj} / {g['name']} 没有写来源")
            for c in g["codes"]:
                total += 1
                code = c.get("code", "")
                if not CODE_RE.match(code):
                    errors.append(f"{subj}.{code} 不符合契约 subject_category 格式")
                if code in seen:
                    errors.append(f"{subj}.{code} 在学科内重复")
                seen.add(code)
                if c.get("maps_to") not in cats:
                    errors.append(f"{subj}.{code} 的 maps_to 不是七大类之一：{c.get('maps_to')}")
                for a in c.get("alternatives", []):
                    if a not in cats or a == c.get("maps_to"):
                        errors.append(f"{subj}.{code} 的备选 {a} 无效")
                if c.get("status") not in ("clear", "disputed"):
                    errors.append(f"{subj}.{code} 的 status 只能是 clear / disputed")
                if c.get("status") == "disputed":
                    disputed += 1
                    if not c.get("note"):
                        errors.append(f"{subj}.{code} 标为 disputed 但没有说明")

    for cat in cats:
        subs = {subj for subj, s in data["subjects"].items()
                for g in s["groups"] for c in g["codes"] if c["maps_to"] == cat}
        if len(subs) < 2:
            warnings.append(f"大类 {cat} 只有 {len(subs)} 科映射，不满足“跨科指标须有至少两科证据”")

    # 契约示例里的学科错因编码，应当都在本表里（不一致只报警告：改契约属于必停项）
    known = {(subj, c["code"]) for subj, s in data["subjects"].items() for g in s["groups"] for c in g["codes"]}
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))

    def walk(node, subject):
        if isinstance(node, dict):
            subject = node.get("context", {}).get("subject", subject) if isinstance(node.get("context"), dict) else subject
            for k, v in node.items():
                if k in ("subject_category", "subject_error_category") and isinstance(v, str) and (subject, v) not in known:
                    warnings.append(f"契约示例里的 {subject}.{v} 不在映射表中")
                walk(v, subject)
        elif isinstance(node, list):
            for v in node:
                walk(v, subject)

    for name, ex in contract["components"]["examples"].items():
        value = ex.get("value", {})
        subject = (value.get("context") or {}).get("subject") if isinstance(value, dict) else None
        if subject is None and isinstance(value, dict):
            refs = [ev.get("item_ref", "") for ev in value.get("evidence", [])]
            subject = refs[0].split(":")[0] if refs else None
        if subject is None:  # 退回到示例名称里的学科名，例如 ReportResponseWenliParent
            subject = next((s for s in data["subjects"] if s.lower() in name.lower()), None)
        walk(value, subject)

    table = render(data)
    md = MD_PATH.read_text(encoding="utf-8")
    if BEGIN not in md or END not in md:
        errors.append("error-taxonomy.md 缺少生成区标记")
    else:
        current = md.split(BEGIN, 1)[1].split(END, 1)[0].strip()
        if current != table.strip():
            if write:
                head, rest = md.split(BEGIN, 1)
                md = head + BEGIN + "\n" + table + "\n" + END + rest.split(END, 1)[1]
                MD_PATH.write_text(md, encoding="utf-8")
                print("taxonomy-check: 已重新生成 error-taxonomy.md 的映射表")
            else:
                errors.append("error-taxonomy.md 的映射表与 YAML 不一致：运行 check_error_taxonomy.py --write 重新生成")

    for w in warnings:
        print(f"taxonomy-check: 警告：{w}")
    if errors:
        print(f"taxonomy-check: 失败（{len(errors)} 项）")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"taxonomy-check: 通过（七大类与契约、数据库一致；{len(data['subjects'])} 科 {total} 个编码，其中 {disputed} 个待教研拍板）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
