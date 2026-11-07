#!/usr/bin/env python3
"""知识服务契约 v0.10（2026-11-07 签字）的检查（specs/kge-12-weknora-integration/spec.md 第一节 B；ADR 16 第 3.2 节）。

由 check_contract.py 在默认运行时一起调用；变异测试见 tests/test_mutations.py 的 V010_MUTATIONS。v0.9 的全部检查照旧由
check_services.py 做；这里只管 v0.10 多出来的部分，并证明它“只做增量”：
1. OpenAPI 3.1 合法，跨文件 $ref 可解析；
2. 只做增量：v0.9 的每个 schema、路径、示例、components.responses / securitySchemes、security、servers 在 v0.10 里逐字不变，
   唯一允许的改动是契约版本常量与示例里的 contract_version 从 0.9.0 换成 0.10.0；新增的只能是 standard.search 的
   路径、schema 与示例；
3. standard.search 的结构：上下文是公共层（不带学生标识）且数据级别固定 L2；六个教材维度必填；请求与命中都不放开多余字段；
   响应基于统一信封、`evidence_ids` 恒为空、`unevaluatedProperties: false`；
4. 响应树没有无约束文本（允许清单只有命中正文与三项来源字段）；请求与响应里没有个人层字段名；命中里没有答案、采分点、
   判定类字段（规则 3）；
5. 示例符合 schema、无 PII；反例（tests/counterexamples_v010.py）全部被拒绝。
"""
from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

from openapi_spec_validator import validate as validate_openapi

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check_services as cs  # noqa: E402

V09 = cs.OPENAPI / "knowledge-service.v0.9.yaml"
V010 = cs.OPENAPI / "knowledge-service.v0.10.yaml"
VERSION = "0.10.0"
OLD = "0.9.0"
NEW_PATH = "/v0/standard.search"
NEW_SCHEMAS = frozenset({"StandardSearchContext", "StandardContentType", "StandardCode", "StandardFilters", "StandardSearchRequest",
                         "StandardContentId", "StandardSource", "StandardPageRef", "StandardHit", "StandardSearchResponse"})
NEW_EXAMPLES = frozenset({"StandardSearchRequest", "StandardSearchResponse", "StandardSearchResponseEmpty"})
REQUIRED_FILTERS = ["subject", "grade", "province", "publisher", "edition", "semester"]
# 响应里允许的自由文本：命中正文（教研签字后交付的公共层内容）与来源字段
FREE_TEXT_ALLOW = {("StandardHit", "text"), ("StandardSource", "title"), ("StandardSource", "org"), ("StandardSource", "locator")}
# 请求里允许的自由文本：检索文本
REQUEST_FREE_TEXT_ALLOW = {("StandardSearchRequest", "query")}
# 命中里不得出现的字段（规则 3：判分只来自验证器或评分细则；WeKnora 的答案与解析只是讲解素材，不以“答案”字段交出去）
ANSWER_NAMES = re.compile(r"(answer|solution|scoring|rubric|verdict|grade_result|correct|key_points?)", re.I)
EXAMPLE_SCHEMA = {"StandardSearchRequest": "StandardSearchRequest", "StandardSearchResponse": "StandardSearchResponse",
                  "StandardSearchResponseEmpty": "StandardSearchResponse"}


def load_docs() -> tuple[dict, dict]:
    return cs.load(V09), cs.load(V010)


def _bump(node):
    """把 v0.9 文档里的契约版本（contract_version 的 const 与示例值）换成 0.10.0，其余原样"""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k == "contract_version" and v == OLD:
                out[k] = VERSION
            elif k == "contract_version" and isinstance(v, dict) and v.get("const") == OLD:
                out[k] = {**v, "const": VERSION}
            else:
                out[k] = _bump(v)
        return out
    if isinstance(node, list):
        return [_bump(v) for v in node]
    return node


def check_additive(v09: dict, v010: dict, errors: list[str]) -> None:
    if v010.get("info", {}).get("version") != VERSION:
        errors.append(f"v0.10 的 info.version 应为 {VERSION}，实为 {v010.get('info', {}).get('version')}")
    so = v010.get("info", {}).get("x-signoff") or {}
    if so.get("status") != "signed" or so.get("version") != VERSION or not so.get("date") or not so.get("signed_by"):
        errors.append("v0.10 已签字（2026-11-07）：info.x-signoff 必须有 signed_by、date、status: signed、version: 0.10.0")
    b = _bump(v09)
    s9, s10 = b["components"]["schemas"], v010["components"]["schemas"]
    for name, sch in s9.items():
        if name not in s10:
            errors.append(f"v0.10 删掉了 v0.9 的 schema {name}")
        elif s10[name] != sch:
            errors.append(f"v0.10 改动了 v0.9 的 schema {name}（只允许增量）")
    if set(s10) - set(s9) != NEW_SCHEMAS:
        errors.append(f"v0.10 新增的 schema 必须正好是 standard.search 的 {sorted(NEW_SCHEMAS)}，实为 {sorted(set(s10) - set(s9))}")
    p9, p10 = b["paths"], v010["paths"]
    for path, op in p9.items():
        if p10.get(path) != op:
            errors.append(f"v0.10 改动或删掉了 v0.9 的路径 {path}")
    if set(p10) - set(p9) != {NEW_PATH}:
        errors.append(f"v0.10 新增的路径只能是 {NEW_PATH}，实为 {sorted(set(p10) - set(p9))}")
    e9, e10 = b["components"]["examples"], v010["components"]["examples"]
    for name, ex in e9.items():
        if name not in e10:
            errors.append(f"v0.10 删掉了 v0.9 的示例 {name}")
        elif e10[name] != ex:
            errors.append(f"v0.10 改动了 v0.9 的示例 {name}（只允许把 contract_version 换成 {VERSION}）")
    if set(e10) - set(e9) != NEW_EXAMPLES:
        errors.append(f"v0.10 新增的示例只能是 standard.search 的 {sorted(NEW_EXAMPLES)}，实为 {sorted(set(e10) - set(e9))}")
    for key in ("responses", "securitySchemes"):
        if v010["components"].get(key) != b["components"].get(key):
            errors.append(f"v0.10 改动了 v0.9 的 components.{key}")
    for key in ("security", "servers", "openapi"):
        if v010.get(key) != b.get(key):
            errors.append(f"v0.10 改动了 v0.9 的 {key}")
    if set(v010["components"]) != set(b["components"]):
        errors.append("v0.10 的 components 分区与 v0.9 不一致")


def _docs(v010: dict) -> dict:
    """check_services 的工具函数按服务键取文档：把 v0.10 放在 knowledge 键下（v0.10 引用的是 plugin-api.v0.9）"""
    docs = cs.load_all()
    docs["knowledge"] = v010
    return docs


def check_structure(v010: dict, errors: list[str]) -> None:
    op = v010.get("paths", {}).get(NEW_PATH, {}).get("post", {})
    if op.get("operationId") != "standard_search":
        errors.append(f"{NEW_PATH} 的 operationId 必须是 standard_search")
    req = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {}).get("$ref")
    resp = op.get("responses", {}).get("200", {}).get("content", {}).get("application/json", {}).get("schema", {}).get("$ref")
    if req != "#/components/schemas/StandardSearchRequest" or resp != "#/components/schemas/StandardSearchResponse":
        errors.append(f"{NEW_PATH} 的请求 / 响应必须是 StandardSearchRequest / StandardSearchResponse（实为 {req} / {resp}）")
    if "security" in op:
        errors.append(f"{NEW_PATH} 不得另设鉴权（沿用顶层平台服务令牌）")
    sch = v010["components"]["schemas"]
    r = sch.get("StandardSearchRequest", {})
    if r.get("additionalProperties") is not False or not {"context", "query", "filters", "content_types"} <= set(r.get("required", [])):
        errors.append("StandardSearchRequest 必须要求 context、query、filters、content_types 且不放开多余字段")
    f = sch.get("StandardFilters", {})
    if f.get("required") != REQUIRED_FILTERS or f.get("additionalProperties") is not False:
        errors.append(f"StandardFilters 必须正好要求 {REQUIRED_FILTERS}（先过滤后检索）且不放开多余字段")
    ctx = sch.get("StandardSearchContext", {})
    parts = ctx.get("allOf", [])
    if not parts or parts[0].get("$ref") != "#/components/schemas/PublicContext":
        errors.append("StandardSearchContext 必须基于 PublicContext（公共层，不带学生标识）")
    over = parts[1].get("properties", {}) if len(parts) > 1 else {}
    if over.get("data_class", {}).get("const") != "L2":
        errors.append("StandardSearchContext 的 data_class 必须固定为 L2（查询文本可能来自学生）")
    if not set(over.get("caller", {}).get("enum", [])) <= {"bff", "orchestrator"} or not over.get("caller"):
        errors.append("StandardSearchContext 的调用方只能是 bff、orchestrator")
    res = sch.get("StandardSearchResponse", {})
    base = res.get("allOf", [{}])[0].get("$ref")
    ev = (res.get("allOf", [{}, {}])[1:] or [{}])[0].get("properties", {}).get("evidence_ids", {})
    if base != "#/components/schemas/ServiceResultBase" or ev.get("maxItems") != 0 or res.get("unevaluatedProperties") is not False:
        errors.append("StandardSearchResponse 必须基于统一信封、evidence_ids 恒为空、unevaluatedProperties: false")
    docs = _docs(v010)
    for leak in cs.free_text_leaks(docs, "knowledge", "StandardSearchResponse", FREE_TEXT_ALLOW):
        errors.append(f"v0.10 响应：{leak}")
    for leak in cs.free_text_leaks(docs, "knowledge", "StandardSearchRequest", REQUEST_FREE_TEXT_ALLOW):
        errors.append(f"v0.10 请求：{leak}")
    for root in ("StandardSearchRequest", "StandardSearchResponse"):
        for name, where in cs.names_in(docs, "knowledge", root, with_values=False):
            if cs.PERSONAL_NAMES.search(name):
                errors.append(f"v0.10 {where}：公共层工具不得出现个人层字段名 {name}（规则 13）")
            if root == "StandardSearchResponse" and ANSWER_NAMES.search(name):
                errors.append(f"v0.10 {where}：命中里不得有答案、采分点、判定类字段 {name}（规则 3）")


def check_examples(v010: dict, errors: list[str]) -> None:
    docs = _docs(v010)
    reg = cs._registry(docs)
    ex = v010["components"]["examples"]
    for name, schema in EXAMPLE_SCHEMA.items():
        if name not in ex:
            errors.append(f"v0.10 缺少示例 {name}")
            continue
        errs = sorted(e.message[:80] for e in cs.validator(docs, "knowledge", schema, reg).iter_errors(ex[name]["value"]))
        if errs:
            errors.append(f"v0.10 示例 {name} 不符合 {schema}：{errs[:3]}")
        blob = repr(ex[name]["value"])
        for label, pat in cs.PII_PATTERNS.items():
            if pat.search(blob):
                errors.append(f"v0.10 示例 {name} 含疑似{label}")


def check_counterexamples(v010: dict, errors: list[str]) -> int:
    sys.path.insert(0, str(HERE.parent / "tests"))
    from counterexamples_v010 import V010_CASES  # noqa: PLC0415
    docs = _docs(v010)
    reg = cs._registry(docs)
    ex = v010["components"]["examples"]
    for case in V010_CASES:
        value = copy.deepcopy(ex[case.base]["value"])
        out = case.mutate(value)
        value = value if out is None else out
        if cs.validator(docs, "knowledge", case.schema, reg).is_valid(value):
            errors.append(f"v0.10 反例未被拒绝：{case.name}（{case.rule}）")
    return len(V010_CASES)


def run_v010_checks(v09: dict | None = None, v010: dict | None = None) -> list[str]:
    if v09 is None or v010 is None:
        v09, v010 = load_docs()
    errors: list[str] = []
    try:
        validate_openapi(copy.deepcopy(v010), base_uri=V010.as_uri())
    except Exception as exc:  # noqa: BLE001 - 打印校验器的原始信息
        errors.append(f"v0.10：OpenAPI 3.1 校验失败：{str(exc)[:400]}")
    for fn in (lambda e: check_additive(v09, v010, e), lambda e: check_structure(v010, e),
               lambda e: check_examples(v010, e), lambda e: check_counterexamples(v010, e)):
        try:
            fn(errors)
        except Exception as exc:  # noqa: BLE001 - 结构不完整时报错而不是崩溃
            errors.append(f"v0.10 检查无法完成：{type(exc).__name__}: {exc}")
    return errors


def summary(v010: dict) -> str:
    sys.path.insert(0, str(HERE.parent / "tests"))
    from counterexamples_v010 import V010_CASES  # noqa: PLC0415
    return (f"知识服务 v0.10（已签字）合法、只做增量（v0.9 的 schema / 路径 / 示例逐项不变，只多 standard.search），"
            f"{len(NEW_EXAMPLES)} 个新示例符合 schema、{len(V010_CASES)} 个反例全部被拒绝")


if __name__ == "__main__":
    errs = run_v010_checks()
    for e in errs:
        print(f"  - {e}")
    print("knowledge-v010:", "失败" if errs else "通过（" + summary(load_docs()[1]) + "）")
    sys.exit(1 if errs else 0)
