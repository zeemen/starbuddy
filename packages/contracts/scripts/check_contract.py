#!/usr/bin/env python3
"""学科插件 API 契约检查。

检查三件事：
1. OpenAPI 3.1 文档本身合法（openapi-spec-validator）；
2. components.examples 里的每个示例都符合它所挂的 schema（jsonschema，启用 format 校验），
   并且示例里引用的 evidence_id 闭合；
3. spec（specs/contracts-plugin-api-v0.9/spec.md）验收标准里能结构化检查的条目。

反向测试见 packages/contracts/tests/test_negative.py：它把契约故意改坏或构造反例，
断言本脚本和 schema 能拒绝。

依赖见 packages/contracts/requirements-dev.txt。缺依赖时退出码 3 并打印“跳过”，
由 Makefile 决定如何呈现；本脚本不会在缺依赖时假装通过。

用法：check_contract.py [契约 yaml 路径]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_SPEC = HERE.parent / "openapi" / "plugin-api.v0.9.yaml"

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
RESPONSES = {
    "diagnose": "DiagnoseResponse", "plan": "PlanResponse", "teach": "TeachResponse",
    "grade": "GradeResponse", "generate_variation": "GenerateVariationResponse", "report": "ReportResponse",
}
# 允许出现“无约束字符串 / 开放对象”的位置：(schema 名, 属性名) -> 允许的响应
# 学生作答原文是证据本身；teach 的讲解文本就是教学内容。grade 不允许任何其他自由文本（CLAUDE.md 规则 3）。
FREE_TEXT_ALLOW = {
    ("EvidencePayloadText", "text"): set(RESPONSES.values()),
    ("TeachSegment", "text"): {"TeachResponse"},
    ("TeachSegment", "latex"): {"TeachResponse"},
    ("TeachSegment", "dsl"): {"TeachResponse"},
    ("TeachSegment", "dsl_version"): {"TeachResponse"},
    ("TeachResult.verified_claims", "claim"): {"TeachResponse"},
}
PII_PATTERNS = {
    "手机号": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "身份证号": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
    "邮箱": re.compile(r"[\w.+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"),
}


def _schemas(doc: dict) -> dict:
    return doc["components"]["schemas"]


def _refname(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def _context_ref(req: dict) -> str | None:
    """找出请求 schema 的 context 用的是 RequestContext 还是 MeteredContext。"""
    ctx = req.get("properties", {}).get("context", {})
    if "$ref" in ctx:
        return _refname(ctx["$ref"])
    for sub in ctx.get("allOf", []):
        if "$ref" in sub:
            return _refname(sub["$ref"])
    return None


def _op_schema_name(doc: dict, path: str, where: str) -> str | None:
    try:
        op = doc["paths"][path]["post"]
        media = (op["requestBody"] if where == "request" else op["responses"]["200"])["content"]["application/json"]
        return _refname(media["schema"]["$ref"])
    except (KeyError, TypeError):
        return None


def _free_text_leaks(doc: dict, root: str) -> list[str]:
    """遍历 root 响应 schema 的全部子树，找出没有约束的字符串字段和开放对象。"""
    schemas = _schemas(doc)
    leaks: list[str] = []
    seen: set[int] = set()

    def visit(node, owner: str, prop: str | None, trail: str) -> None:
        if not isinstance(node, dict) or id(node) in seen:
            return
        seen.add(id(node))
        if "$ref" in node:
            name = _refname(node["$ref"])
            visit(schemas.get(name, {}), name, None, f"{trail}->{name}")
        key = (owner, prop) if prop else None
        allowed = key in FREE_TEXT_ALLOW and root in FREE_TEXT_ALLOW[key]
        types = node.get("type")
        types = types if isinstance(types, list) else [types]
        if "string" in types and not allowed and not any(k in node for k in ("enum", "const", "pattern", "format")):
            leaks.append(f"{trail}：无约束字符串（没有 enum / const / pattern / format）")
        if node.get("additionalProperties") is True and not allowed:
            leaks.append(f"{trail}：开放对象（additionalProperties: true）")
        if node.get("patternProperties"):
            leaks.append(f"{trail}：patternProperties 可以绕过字段白名单")
        for k in ("allOf", "anyOf", "oneOf"):
            for sub in node.get(k, []):
                visit(sub, owner, prop, trail)
        for k in ("if", "then", "else", "not", "contains", "items", "additionalProperties"):
            if isinstance(node.get(k), dict):
                visit(node[k], owner, prop, trail)
        for name, sub in node.get("properties", {}).items():
            child_owner = owner if prop is None else f"{owner}.{prop}"
            visit(sub, child_owner, name, f"{trail}.{name}")

    visit(schemas[root], root, None, root)
    return leaks


def check_structure(doc: dict, errors: list[str]) -> None:
    schemas = _schemas(doc)
    base = schemas.get("PluginResultBase", {})
    missing = ENVELOPE_REQUIRED - set(base.get("required", []))
    if missing:
        errors.append(f"PluginResultBase 缺少必填字段：{sorted(missing)}")

    for m in METHODS:
        path = f"/{m}"
        if path not in doc.get("paths", {}):
            errors.append(f"缺少方法路径 {path}")
            continue
        resp_name = _op_schema_name(doc, path, "response")
        if resp_name != RESPONSES[m]:
            errors.append(f"{m} 的 200 响应应为 {RESPONSES[m]}，实际为 {resp_name}")
            continue
        resp = schemas[resp_name]
        refs = [s.get("$ref", "") for s in resp.get("allOf", [])]
        if "#/components/schemas/PluginResultBase" not in refs:
            errors.append(f"{m} 的 200 响应没有 $ref 统一信封 PluginResultBase")
        if resp.get("unevaluatedProperties") is not False:
            errors.append(f"{resp_name} 必须设 unevaluatedProperties: false（信封外不允许夹带字段）")

        req_name = _op_schema_name(doc, path, "request")
        ctx = _context_ref(schemas.get(req_name, {})) if req_name else None
        if m in METERED and ctx != "MeteredContext":
            errors.append(f"{m} 是消耗类方法，context 必须基于 MeteredContext（usage_id 必填）")
        if m not in METERED and ctx != "RequestContext":
            errors.append(f"{m} 是非消耗类方法，context 应基于 RequestContext")

    ctx = schemas.get("RequestContext", {})
    if "idempotency_key" not in ctx.get("required", []):
        errors.append("RequestContext.idempotency_key 必须必填")
    metered = schemas.get("MeteredContext", {})
    if not any("usage_id" in s.get("required", []) for s in metered.get("allOf", [])):
        errors.append("MeteredContext 没有把 usage_id 设为必填")
    if "semantics" not in base.get("required", []):
        errors.append("PluginResultBase.semantics 必须必填（内容准入）")

    for name in RESPONSES.values():
        if name in schemas:
            errors.extend(_free_text_leaks(doc, name))

    job = schemas.get("JobStatus", {})
    bound = {_refname(c.get("then", {}).get("properties", {}).get("response", {}).get("$ref", ""))
             for c in job.get("allOf", [])}
    if not {"GradeResponse", "DiagnoseResponse"} <= bound:
        errors.append("JobStatus.response 必须按 method 绑定到 GradeResponse / DiagnoseResponse")


def _collect_evidence_refs(node, acc: set[str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "evidence_ids" and isinstance(v, list):
                acc.update(v)
            else:
                _collect_evidence_refs(v, acc)
    elif isinstance(node, list):
        for v in node:
            _collect_evidence_refs(v, acc)


def check_examples(doc: dict, errors: list[str]) -> None:
    registry = Registry().with_resource("urn:contract", Resource(contents=doc, specification=DRAFT202012))
    fmt = FormatChecker()
    examples = doc.get("components", {}).get("examples", {})

    def validate(schema_name: str, value) -> list[str]:
        v = Draft202012Validator({"$ref": f"urn:contract#/components/schemas/{schema_name}"},
                                 registry=registry, format_checker=fmt)
        return [f"{'/'.join(map(str, e.absolute_path))}：{e.message[:200]}" for e in v.iter_errors(value)]

    used: set[str] = set()
    for path, item in doc.get("paths", {}).items():
        for op in item.values():
            if not isinstance(op, dict):
                continue
            medias = []
            body = op.get("requestBody", {}).get("content", {}).get("application/json")
            if body:
                medias.append(body)
            for resp in op.get("responses", {}).values():
                if isinstance(resp, dict) and "$ref" in resp:
                    resp = doc["components"]["responses"][_refname(resp["$ref"])]
                content = resp.get("content", {}).get("application/json") if isinstance(resp, dict) else None
                if content:
                    medias.append(content)
            for media in medias:
                if "$ref" not in media.get("schema", {}):
                    continue
                name = _refname(media["schema"]["$ref"])
                for ex in media.get("examples", {}).values():
                    ex_name = _refname(ex["$ref"])
                    used.add(ex_name)
                    value = examples[ex_name]["value"]
                    for msg in validate(name, value):
                        errors.append(f"示例 {ex_name} 不符合 {name}：{msg}")
                    if name.endswith("Response") and isinstance(value, dict) and "result" in value:
                        refs: set[str] = set()
                        _collect_evidence_refs(value.get("result"), refs)
                        envelope = set(value.get("evidence_ids", []))
                        drafts = {e["evidence_id"] for e in value.get("evidence", [])}
                        if not refs <= envelope:
                            errors.append(f"示例 {ex_name}：result 引用的 evidence_id 不在信封 evidence_ids 里：{sorted(refs - envelope)}")
                        if not drafts <= envelope:
                            errors.append(f"示例 {ex_name}：evidence 草稿没有列进信封 evidence_ids：{sorted(drafts - envelope)}")

    unused = set(examples) - used
    if unused:
        errors.append(f"有示例没有挂到任何操作上：{sorted(unused)}")
    for m in METHODS:
        try:
            has = doc["paths"][f"/{m}"]["post"]["requestBody"]["content"]["application/json"].get("examples")
        except KeyError:
            has = None
        if not has:
            errors.append(f"{m} 没有请求示例")

    raw = yaml.safe_dump(examples, allow_unicode=True)
    for label, pat in PII_PATTERNS.items():
        hit = pat.search(raw)
        if hit:
            errors.append(f"示例里出现疑似{label}：{hit.group(0)}（示例只能用合成数据）")


def check_counterexamples(doc: dict, errors: list[str]) -> None:
    """反例集：每个反例都必须被 schema 拒绝（packages/contracts/tests/counterexamples.py）。"""
    sys.path.insert(0, str(HERE.parent / "tests"))
    from counterexamples import CASES  # noqa: PLC0415

    registry = Registry().with_resource("urn:contract", Resource(contents=doc, specification=DRAFT202012))
    examples = doc["components"]["examples"]
    for case in CASES:
        value = case.build(examples)
        v = Draft202012Validator({"$ref": f"urn:contract#/components/schemas/{case.schema}"},
                                 registry=registry, format_checker=FormatChecker())
        if v.is_valid(value):
            errors.append(f"反例未被拒绝：{case.name}（{case.rule}）")


def run_checks(doc: dict) -> list[str]:
    errors: list[str] = []
    try:
        validate_openapi(doc)
    except Exception as exc:  # noqa: BLE001 - 打印校验器的原始信息
        errors.append(f"OpenAPI 3.1 校验失败：{str(exc)[:500]}")
    for fn in (check_structure, check_examples, check_counterexamples):
        try:
            fn(doc, errors)
        except Exception as exc:  # noqa: BLE001 - 结构不完整时报错而不是崩溃
            errors.append(f"{fn.__name__} 无法完成：{type(exc).__name__}: {exc}")
    return errors


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def main() -> int:
    spec = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SPEC
    doc = load(spec)
    errors = run_checks(doc)
    if errors:
        print(f"contract-test: 失败（{len(errors)} 项）")
        for e in errors:
            print(f"  - {e}")
        return 1
    n_ex = len(doc["components"]["examples"])
    sys.path.insert(0, str(HERE.parent / "tests"))
    from counterexamples import CASES  # noqa: PLC0415
    print(f"contract-test: 通过（OpenAPI 3.1 合法；{n_ex} 个示例符合 schema 且证据引用闭合；"
          f"{len(CASES)} 个反例全部被拒绝；六方法信封、计量字段、无约束文本扫描、JobStatus 绑定、示例无 PII）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
