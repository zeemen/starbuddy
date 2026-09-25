#!/usr/bin/env python3
"""学科插件 API 契约检查。

检查三件事：
1. OpenAPI 3.1 文档本身合法（openapi-spec-validator）；
2. components.examples 里的每个示例都符合它所挂的 schema（jsonschema，启用 format 校验）；
3. spec（specs/contracts-plugin-api-v0.9/spec.md）验收标准里能结构化检查的条目。

依赖见 packages/contracts/requirements-dev.txt。缺依赖时退出码 3 并打印“跳过”，
由 Makefile 决定如何呈现；本脚本不会在缺依赖时假装通过。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent / "openapi" / "plugin-api.v0.9.yaml"

try:
    import yaml
    from jsonschema import Draft202012Validator, FormatChecker
    from openapi_spec_validator import validate as validate_openapi
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
except ImportError as exc:  # pragma: no cover
    print(f"contract-test: 跳过（缺少依赖 {exc.name}；pip install -r packages/contracts/requirements-dev.txt）")
    sys.exit(3)

METHODS = ["diagnose", "plan", "teach", "grade", "generate_variation", "report"]
METERED = {"diagnose", "teach", "grade"}  # 完整方案 5.8；teach 是否拆分见 spec 待人确认 #2
ENVELOPE_REQUIRED = {
    "result", "evidence_ids", "confidence", "source_version", "model_version", "human_review_required",
}
# 示例里不允许出现的疑似真实个人信息（CLAUDE.md 规则 8）
PII_PATTERNS = {
    "手机号": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "身份证号": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
    "邮箱": re.compile(r"[\w.+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"),
}
# grade 结果里禁止出现的自由文本字段名（CLAUDE.md 规则 3）
FREE_TEXT_NAMES = {"comment", "feedback", "explanation", "overall_comment", "note", "text", "narrative"}

errors: list[str] = []


def fail(msg: str) -> None:
    errors.append(msg)


def schema_ref(doc: dict, name: str) -> dict:
    return doc["components"]["schemas"][name]


def request_schema_name(doc: dict, path: str) -> str:
    ref = doc["paths"][path]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    return ref.rsplit("/", 1)[-1]


def response_schema_name(doc: dict, path: str) -> str:
    ref = doc["paths"][path]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    return ref.rsplit("/", 1)[-1]


def walk_property_names(schema: dict, doc: dict, seen: set[str]) -> set[str]:
    """收集一个 schema（含 $ref、allOf、items）里出现的全部属性名。"""
    names: set[str] = set()
    if not isinstance(schema, dict):
        return names
    if "$ref" in schema:
        target = schema["$ref"].rsplit("/", 1)[-1]
        if target in seen:
            return names
        seen.add(target)
        return walk_property_names(schema_ref(doc, target), doc, seen)
    for key in ("allOf", "anyOf", "oneOf"):
        for sub in schema.get(key, []):
            names |= walk_property_names(sub, doc, seen)
    for name, sub in schema.get("properties", {}).items():
        names.add(name)
        names |= walk_property_names(sub, doc, seen)
    if "items" in schema:
        names |= walk_property_names(schema["items"], doc, seen)
    return names


def check_structure(doc: dict) -> None:
    base = schema_ref(doc, "PluginResultBase")
    missing = ENVELOPE_REQUIRED - set(base.get("required", []))
    if missing:
        fail(f"PluginResultBase 缺少必填字段：{sorted(missing)}")

    for m in METHODS:
        path = f"/{m}"
        if path not in doc["paths"]:
            fail(f"缺少方法路径 {path}")
            continue
        resp = schema_ref(doc, response_schema_name(doc, path))
        refs = [s.get("$ref", "") for s in resp.get("allOf", [])]
        if "#/components/schemas/PluginResultBase" not in refs:
            fail(f"{m} 的 200 响应没有 $ref 统一信封 PluginResultBase")

        req = schema_ref(doc, request_schema_name(doc, path))
        ctx_ref = req["properties"]["context"]["$ref"].rsplit("/", 1)[-1]
        if m in METERED and ctx_ref != "MeteredContext":
            fail(f"{m} 是消耗类方法，context 必须是 MeteredContext（usage_id 必填）")
        if m not in METERED and ctx_ref != "RequestContext":
            fail(f"{m} 是非消耗类方法，context 应为 RequestContext，并在描述中写明理由")

    ctx = schema_ref(doc, "RequestContext")
    if "idempotency_key" not in ctx.get("required", []):
        fail("RequestContext.idempotency_key 必须必填")
    metered = schema_ref(doc, "MeteredContext")
    if not any("usage_id" in s.get("required", []) for s in metered.get("allOf", [])):
        fail("MeteredContext 没有把 usage_id 设为必填")

    grade_names = walk_property_names(schema_ref(doc, "GradeResult"), doc, set())
    leaked = grade_names & FREE_TEXT_NAMES
    if leaked:
        fail(f"GradeResult 里出现自由文本字段 {sorted(leaked)}，违反“判分只来自验证器或 Rubric”")


def check_examples(doc: dict) -> None:
    registry = Registry().with_resource("urn:contract", Resource(contents=doc, specification=DRAFT202012))
    fmt = FormatChecker()

    def validator_for(schema_name: str) -> Draft202012Validator:
        return Draft202012Validator(
            {"$ref": f"urn:contract#/components/schemas/{schema_name}"}, registry=registry, format_checker=fmt
        )

    used: set[str] = set()
    for path, item in doc["paths"].items():
        for op in item.values():
            if not isinstance(op, dict):
                continue
            pairs = []
            body = op.get("requestBody", {}).get("content", {}).get("application/json")
            if body:
                pairs.append(body)
            for resp in op.get("responses", {}).values():
                content = resp.get("content", {}).get("application/json") if isinstance(resp, dict) else None
                if content:
                    pairs.append(content)
            for media in pairs:
                name = media["schema"]["$ref"].rsplit("/", 1)[-1]
                for ex in media.get("examples", {}).values():
                    ex_name = ex["$ref"].rsplit("/", 1)[-1]
                    used.add(ex_name)
                    value = doc["components"]["examples"][ex_name]["value"]
                    for err in validator_for(name).iter_errors(value):
                        loc = "/".join(str(p) for p in err.absolute_path)
                        fail(f"示例 {ex_name} 不符合 {name}：{loc}：{err.message[:200]}")

    unused = set(doc["components"]["examples"]) - used
    if unused:
        fail(f"有示例没有挂到任何操作上：{sorted(unused)}")

    for m in METHODS:
        if not doc["paths"][f"/{m}"]["post"]["requestBody"]["content"]["application/json"].get("examples"):
            fail(f"{m} 没有请求示例")

    raw = yaml.safe_dump(doc["components"]["examples"], allow_unicode=True)
    for label, pat in PII_PATTERNS.items():
        hit = pat.search(raw)
        if hit:
            fail(f"示例里出现疑似{label}：{hit.group(0)}（示例只能用合成数据）")


def main() -> int:
    doc = yaml.safe_load(SPEC.read_text(encoding="utf-8"))
    try:
        validate_openapi(doc)
    except Exception as exc:  # noqa: BLE001 - 打印校验器的原始信息
        fail(f"OpenAPI 3.1 校验失败：{str(exc)[:500]}")
    check_structure(doc)
    check_examples(doc)

    if errors:
        print(f"contract-test: 失败（{len(errors)} 项）")
        for e in errors:
            print(f"  - {e}")
        return 1
    n_ex = len(doc["components"]["examples"])
    print(f"contract-test: 通过（OpenAPI 3.1 合法；{n_ex} 个示例符合 schema；六方法信封、计量字段、判分无自由文本、示例无 PII）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
