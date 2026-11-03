#!/usr/bin/env python3
"""节点 ID 规范检查（specs/kge-schema-v0.9/spec.md 验收最后一条）。

比对 docs/design/kge/id-and-segments.md 与迁移 db/migrations/0011_kge__knowledge_map.up.sql：
  1. 文档里的节点 ID 正则与迁移中 kge.node_id 域的 CHECK 逐字一致；
  2. 文档的正例、反例表逐条符合该正则；
  3. 文档的学科前缀对照表与迁移中 kge.subject_prefix 的 CASE 一致；
  3a. 知识服务契约的 NodeId pattern 与迁移逐字一致；
  4. 自检：把正则、前缀改错的反例必须被本脚本发现。
退出码：0 通过；1 失败。只用标准库。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DOC = ROOT / "docs/design/kge/id-and-segments.md"
MIGRATION = ROOT / "db/migrations/0011_kge__knowledge_map.up.sql"
CONTRACT = ROOT / "packages/contracts/openapi/knowledge-service.v0.9.yaml"


def section(text: str, name: str) -> str | None:
    m = re.search(rf"<!-- {name}:start -->(.*?)<!-- {name}:end -->", text, re.S)
    return m.group(1) if m else None


def table_rows(block: str) -> list[list[str]]:
    rows = []
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("|") or re.fullmatch(r"\|[\s\-|]+\|", line):
            continue
        rows.append([c.strip() for c in line.strip("|").split("|")])
    return rows[1:]  # 去掉表头


def contract_regex(text: str) -> str | None:
    """知识服务契约里 NodeId 的 pattern（只用标准库：按固定写法截取，不解析 YAML）。"""
    m = re.search(r"^    NodeId:\n(?:      .*\n)*?      pattern: '([^']+)'", text, re.M)
    return m.group(1) if m else None


def check(doc: str, sql: str, contract: str | None = None) -> list[str]:
    errors: list[str] = []
    m = re.search(r"CREATE DOMAIN kge\.node_id AS text\s+CHECK \(VALUE ~ '([^']+)'\)", sql)
    if not m:
        return ["迁移里找不到 kge.node_id 域的正则"]
    sql_regex = m.group(1)
    block = section(doc, "node-id-regex")
    doc_m = re.search(r"`([^`]+)`", block or "")
    if not doc_m:
        return ["文档里找不到节点 ID 正则（node-id-regex 标记）"]
    if doc_m.group(1) != sql_regex:
        errors.append(f"文档正则 {doc_m.group(1)} 与迁移 {sql_regex} 不一致")
    pattern = re.compile(sql_regex)
    if contract is not None:
        c = contract_regex(contract)
        if c is None:
            errors.append("知识服务契约里找不到 NodeId 的 pattern")
        elif c != sql_regex:
            errors.append(f"知识服务契约 NodeId 的 pattern {c} 与迁移 {sql_regex} 不一致")

    examples = table_rows(section(doc, "node-id-examples") or "")
    if sum(1 for r in examples if r[1] == "合法") < 3 or sum(1 for r in examples if r[1] == "不合法") < 3:
        errors.append("正例与反例各至少 3 条")
    for ident, verdict, *_ in examples:
        ident = ident.strip("`")
        ok = pattern.fullmatch(ident) is not None
        if verdict not in ("合法", "不合法"):
            errors.append(f"例子 {ident} 的“是否合法”只能写合法或不合法")
        elif ok != (verdict == "合法"):
            errors.append(f"例子 {ident} 标为{verdict}，但正则判定为{'合法' if ok else '不合法'}")

    case = re.search(r"CREATE FUNCTION kge\.subject_prefix\(.*?\$\$(.*?)\$\$", sql, re.S)
    sql_map = dict(re.findall(r"WHEN '([a-z]+)' THEN '([A-Z]+)'", case.group(1))) if case else {}
    doc_map = {r[0]: r[1] for r in table_rows(section(doc, "subject-prefix") or "")}
    if not sql_map:
        errors.append("迁移里找不到 kge.subject_prefix 的对照")
    if sql_map != doc_map:
        errors.append(f"学科前缀对照不一致：文档 {doc_map}，迁移 {sql_map}")
    for prefix in sql_map.values():
        if not pattern.fullmatch(prefix + ".X"):
            errors.append(f"学科前缀 {prefix} 不符合节点 ID 正则的首段")
    return errors


def self_test(doc: str, sql: str) -> list[str]:
    """反例：把文档或迁移改坏，检查必须失败。"""
    mutants = {
        "文档正则改动一个字符": (doc.replace("{0,31}", "{0,30}", 1), sql),
        "迁移正则放宽到小写": (doc, sql.replace("^[A-Z][A-Z0-9]{1,9}", "^[A-Za-z][A-Z0-9]{1,9}", 1)),
        "反例标成合法": (doc.replace("| `math.func.quad.vertex_form` | 不合法 |", "| `math.func.quad.vertex_form` | 合法 |"), sql),
        "文档前缀改错": (doc.replace("| wenli | CHN |", "| wenli | ZH |"), sql),
        "迁移前缀少一科": (doc, sql.replace("WHEN 'zhixing' THEN 'MORAL' ", "", 1)),
    }
    contract = CONTRACT.read_text(encoding="utf-8")
    bad_contract = contract.replace("{1,9}(\\.[A-Z0-9]", "{1,9}(\\.[A-Za-z0-9]", 1)
    out = []
    for name, (d, s) in mutants.items():
        if d == doc and s == sql:
            out.append(f"自检反例“{name}”没有改动任何内容（替换目标不存在）")
        elif not check(d, s):
            out.append(f"自检反例“{name}”没有被发现")
    if bad_contract == contract:
        out.append("自检反例“契约正则放宽”没有改动任何内容（替换目标不存在）")
    elif not check(doc, sql, bad_contract):
        out.append("自检反例“契约正则放宽”没有被发现")
    return out


def main() -> int:
    doc = DOC.read_text(encoding="utf-8")
    sql = MIGRATION.read_text(encoding="utf-8")
    errors = check(doc, sql, CONTRACT.read_text(encoding="utf-8")) + self_test(doc, sql)
    for e in errors:
        print(f"kge-id-check: {e}")
    n = len(table_rows(section(doc, "node-id-examples") or ""))
    print(f"kge-id-check: {'失败' if errors else '通过'}（文档、迁移、知识服务契约三处正则一致，{n} 个例子，6 个自检反例）")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
