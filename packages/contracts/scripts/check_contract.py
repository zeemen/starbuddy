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
不带参数时检查 v0.9 与 v1.0 候选版两份（迁移期两份并存，specs/plugin-api-v1.0/spec.md），外加 v1.0 的破坏性变更反例。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OPENAPI = HERE.parent / "openapi"
DEFAULT_SPEC = OPENAPI / "plugin-api.v0.9.yaml"
V1_SPEC = OPENAPI / "plugin-api.v1.0.yaml"
SPECS = [DEFAULT_SPEC, V1_SPEC]

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
    ("EvidencePayloadText", "text"): {*RESPONSES.values(), "JobStatus"},
    ("TeachSegment", "text"): {"TeachResponse"},
    ("TeachSegment", "latex"): {"TeachResponse"},
    ("TeachSegment", "dsl"): {"TeachResponse"},
    ("TeachResult.verified_claims", "claim"): {"TeachResponse"},
    ("TeachResult.verified_statements", "statement"): {"TeachResponse"},  # v1.0 改名（评审 sie-#4）
    ("Problem", "title"): {"JobStatus"},
    ("Problem", "detail"): {"JobStatus"},
}
# 自由文本扫描的根：六个响应 + 异步任务状态（失败时带 Problem）
SCAN_ROOTS = [*RESPONSES.values(), "JobStatus"]
PII_PATTERNS = {
    "手机号": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "身份证号": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
    "邮箱": re.compile(r"[\w.+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"),
}


def _registry(doc: dict, path: Path) -> tuple[Registry, str]:
    """示例与反例校验用的注册表：契约本身按文件 URI 登记，同目录的其他契约也登记，跨文件 $ref（v1.0 引用两份服务契约）能解析。"""
    registry = Registry()
    for other in sorted(OPENAPI.glob("*.yaml")):
        if other.resolve() != path.resolve():
            registry = registry.with_resource(other.resolve().as_uri(), Resource(contents=load(other), specification=DRAFT202012))
    uri = path.resolve().as_uri()
    return registry.with_resource(uri, Resource(contents=doc, specification=DRAFT202012)), uri


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
        pat = node.get("pattern")
        if isinstance(pat, str) and not (pat.startswith("^") and pat.endswith("$")):
            leaks.append(f"{trail}：pattern 没有同时锚定开头和结尾，可以夹带任意文本：{pat}")
        if "string" in types and allowed and "maxLength" not in node:
            leaks.append(f"{trail}：允许的自由文本也必须有 maxLength")
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

    for name in SCAN_ROOTS:
        if name in schemas:
            errors.extend(_free_text_leaks(doc, name))

    # 规则 1/2：适配器需要的数据由平台放进请求（内容，不是 ID），适配器不查库
    need_content = {
        "DiagnoseRequest": {"prior_evidence": "EvidenceRecord"},
        "TeachRequest": {"recent_evidence": "EvidenceRecord"},
        "ReportRequest": {"evidence": "EvidenceRecord", "error_attributions": "ErrorAttribution",
                          "mastery_history": None},
    }
    for req_name, fields in need_content.items():
        props = schemas.get(req_name, {}).get("properties", {})
        for field, item_ref in fields.items():
            if field not in props:
                errors.append(f"{req_name} 缺少 {field}：平台必须把数据内容放进请求（规则 1/2）")
            elif item_ref and _refname(props[field].get("items", {}).get("$ref", "")) != item_ref:
                errors.append(f"{req_name}.{field} 的元素必须是 {item_ref}（传内容，不是只传 ID）")
        leaked_ids = [f for f in props if f.endswith("evidence_ids")]
        if leaked_ids:
            errors.append(f"{req_name} 只传证据 ID（{leaked_ids}），适配器会被迫查库（规则 1/2）")

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


def check_examples(doc: dict, errors: list[str], path: Path = DEFAULT_SPEC) -> None:
    registry, uri = _registry(doc, path)
    fmt = FormatChecker()
    examples = doc.get("components", {}).get("examples", {})

    def validate(schema_name: str, value) -> list[str]:
        v = Draft202012Validator({"$ref": f"{uri}#/components/schemas/{schema_name}"},
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
                for ctype in ("application/json", "application/problem+json"):  # 错误体示例也要校验（v1.0 新错误码）
                    content = resp.get("content", {}).get(ctype) if isinstance(resp, dict) else None
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


def _validator(registry: Registry, uri: str, schema: str, formats: bool = True) -> Draft202012Validator:
    return Draft202012Validator({"$ref": f"{uri}#/components/schemas/{schema}"}, registry=registry,
                                format_checker=FormatChecker() if formats else None)


def check_counterexamples(doc: dict, errors: list[str], path: Path = DEFAULT_SPEC) -> None:
    """反例集：每个反例都必须被 schema 拒绝（packages/contracts/tests/counterexamples.py）。
    v1.0 另有自己的反例（counterexamples_v1.V1_CASES），只对 v1.0 校验。"""
    sys.path.insert(0, str(HERE.parent / "tests"))
    from counterexamples import CASES  # noqa: PLC0415
    from counterexamples_v1 import V1_CASES  # noqa: PLC0415

    registry, uri = _registry(doc, path)
    examples = doc["components"]["examples"]
    cases = [*CASES, *(V1_CASES if doc["info"]["version"] == "1.0.0" else [])]
    for case in cases:
        value = case.build(examples)
        if _validator(registry, uri, case.schema).is_valid(value):
            errors.append(f"反例未被拒绝：{case.name}（{case.rule}）")


def check_breaking(v09: dict, v1: dict, errors: list[str]) -> int:
    """v1.0 的每条破坏性变更：同一个实例 v0.9 接受、v1.0 拒绝（specs/plugin-api-v1.0/spec.md 验收第 2 条）。"""
    sys.path.insert(0, str(HERE.parent / "tests"))
    from counterexamples_v1 import BREAKING, to_v1  # noqa: PLC0415

    reg0, uri0 = _registry(v09, DEFAULT_SPEC)
    reg1, uri1 = _registry(v1, V1_SPEC)
    for case in BREAKING:
        value = case.build(v09["components"]["examples"])
        if not _validator(reg0, uri0, case.schema, case.formats).is_valid(value):
            errors.append(f"破坏性变更用例在 v0.9 下就不合法（证明不了是 v1.0 收紧）：{case.name}")
        if _validator(reg1, uri1, case.schema, case.formats).is_valid(to_v1(value, case.convert_keys)):
            errors.append(f"破坏性变更没有生效（v1.0 仍接受）：{case.name}（{case.rule}）")
        # 对照：不做这处修改的同一实例升到 v1.0 写法后，v1.0 必须接受，才能证明拒绝来自这条收紧（契约审查）
        control = case.control(v09["components"]["examples"]) if case.control else None
        if control is not None and not _validator(reg1, uri1, case.schema, case.formats).is_valid(to_v1(control)):
            errors.append(f"破坏性变更用例的对照实例在 v1.0 下不合法，证明不了拒绝原因：{case.name}")
    return len(BREAKING)


def check_versions(doc: dict, errors: list[str]) -> None:
    """info.version、RequestContext、信封、能力声明四处的契约版本必须一致（README“版本策略”）。"""
    version = doc["info"]["version"]
    schemas = _schemas(doc)
    found = {
        "RequestContext": schemas["RequestContext"]["properties"]["contract_version"].get("const"),
        "PluginResultBase": schemas["PluginResultBase"]["properties"]["contract_version"].get("const"),
        "Capabilities": schemas["Capabilities"]["properties"]["contract_version"].get("const"),
    }
    for where, v in found.items():
        if str(v) != version:
            errors.append(f"{where}.contract_version（{v}）与 info.version（{version}）不一致")


V1_FIELDS = {  # v1.0 与两个核心、考情库、输入通道的衔接字段（评审 svc-#1、#5、#6、pol-#1、ADR 15）
    "DiagnoseRequest": ["learner_context", "node_path"],
    "PlanRequest": ["learner_context", "node_path", "exam_config"],
    "TeachRequest": ["knowledge_page"],
    "GradeRequest": ["scoring_points", "answer_evidence_ref", "evidence_level", "exam_config"],
    "GenerateVariationRequest": ["item_family", "exam_config"],
    "ReportRequest": ["learner_context"],
}
V1_CODES = {"UNAUTHENTICATED", "MODE_NOT_SUPPORTED"}  # 评审 N1、N9


def check_v1_rules(doc: dict, errors: list[str]) -> None:
    """v1.0 才有的结构要求：衔接字段都在且不是必填（迁移期平台未必能填）；新增错误码在枚举里；预测不在报告里。"""
    if doc["info"]["version"] != "1.0.0":
        return
    schemas = _schemas(doc)
    for req, fields in V1_FIELDS.items():
        props = schemas.get(req, {}).get("properties", {})
        for f in fields:
            if f not in props:
                errors.append(f"{req} 缺少 v1.0 衔接字段 {f}")
            if f in schemas.get(req, {}).get("required", []):
                errors.append(f"{req}.{f} 必须是可选字段（平台上线对应服务后才填）")
    missing = V1_CODES - set(schemas["Problem"]["properties"]["code"]["enum"])
    if missing:
        errors.append(f"Problem.code 缺少 v1.0 新增的错误码：{sorted(missing)}")
    if "forecast" in schemas["ReportFields"].get("properties", {}):
        errors.append("ReportFields 不能再有 forecast（评审 sie-#2：预测只由洞察服务提供）")


def run_checks(doc: dict, path: Path = DEFAULT_SPEC) -> list[str]:
    errors: list[str] = []
    try:
        validate_openapi(doc, base_uri=path.resolve().as_uri())
    except Exception as exc:  # noqa: BLE001 - 打印校验器的原始信息
        errors.append(f"OpenAPI 3.1 校验失败：{str(exc)[:500]}")
    for fn in (check_structure, check_versions, check_v1_rules):
        try:
            fn(doc, errors)
        except Exception as exc:  # noqa: BLE001 - 结构不完整时报错而不是崩溃
            errors.append(f"{fn.__name__} 无法完成：{type(exc).__name__}: {exc}")
    for fn in (check_examples, check_counterexamples):
        try:
            fn(doc, errors, path)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{fn.__name__} 无法完成：{type(exc).__name__}: {exc}")
    return errors


INTERNAL_SPEC = HERE.parent / "openapi" / "learning-internal.v0.yaml"
INTERNAL_SPECS = [INTERNAL_SPEC, HERE.parent / "openapi" / "learning-internal.v1.yaml"]


def check_internal(errors: list[str]) -> int:
    """学习服务内部 API（BFF → 学习服务）v0 与 v1：OpenAPI 合法，跨文件 $ref 都能解析（冲刺 1-A 范围第 5 条）；
    v1 的示例符合 schema，信封与摘要的反例被拒绝（specs/evidence-attributes-sprint1）。"""
    n = sum(_check_internal_one(spec, errors) for spec in INTERNAL_SPECS)
    _check_internal_v1_examples(errors)
    return n


# v1 信封与摘要的反例：每条都从合法示例出发只改一处，必须被拒绝
INTERNAL_V1_COUNTER = [
    ("信封缺 ingest", "GradeIngest", "GradeIngestKeypad", lambda v: v.pop("ingest")),
    ("ingest 缺输入通道", "GradeIngest", "GradeIngestKeypad", lambda v: v["ingest"].pop("channel")),
    ("输入通道拼错", "GradeIngest", "GradeIngestKeypad", lambda v: v["ingest"].__setitem__("channel", "camera")),
    ("用途不在枚举里", "GradeIngest", "GradeIngestKeypad", lambda v: v["ingest"].__setitem__("purpose", "exam")),
    ("ingest 夹带作答情境（由平台推导，不由调用方指定）", "GradeIngest", "GradeIngestKeypad",
     lambda v: v["ingest"].__setitem__("evidence_kind", "transfer")),
    ("信封顶层夹带多余字段", "GradeIngest", "GradeIngestKeypad", lambda v: v.__setitem__("student_name", "（合成）")),
    ("插件请求直接放在顶层（旧写法）", "GradeIngest", "GradeIngestKeypad", lambda v: v.update(v.pop("request"))),
    ("摘要的证据等级是 D", "GradeSummary", "GradeSummaryLevelC", lambda v: v.__setitem__("evidence_level", "D")),
    ("grade 的证据等级在 ingest 里再给一次", "GradeIngest", "GradeIngestKeypad", lambda v: v["ingest"].__setitem__("evidence_level", "A")),
    ("ingest 的证据等级是 D", "DiagnoseIngest", "DiagnoseIngestCanvasC", lambda v: v["ingest"].__setitem__("evidence_level", "D")),
    ("诊断信封缺 ingest", "DiagnoseIngest", "DiagnoseIngestCanvasC", lambda v: v.pop("ingest")),
    ("诊断信封顶层夹带多余字段", "DiagnoseIngest", "DiagnoseIngestCanvasC", lambda v: v.__setitem__("note", "（合成）")),
    ("诊断信封里放判分请求", "DiagnoseIngest", "DiagnoseIngestCanvasC",
     lambda v: (v["request"].pop("mode"), v["request"].__setitem__("attempt", v["request"].pop("attempts")[0]),
                v["request"].__setitem__("rubric_version", "aya-symbolic-ts@0.1.0"))),
    ("诊断摘要的证据等级是 D", "DiagnoseSummary", "DiagnoseSummaryLevelC", lambda v: v.__setitem__("evidence_level", "D")),
    ("摘要的是否写入学习者模型不是布尔", "GradeSummary", "GradeSummaryLevelC", lambda v: v.__setitem__("learner_model_updated", "no")),
    # 模型路由接口（ADP-道法-01；契约审查建议 4）
    ("路由请求夹带未知字段", "ModelRouteRequest", "ModelRouteRequestZhixing", lambda v: v.__setitem__("vendor", "fake-a")),
    ("路由请求的幂等键不是 UUIDv4", "ModelRouteRequest", "ModelRouteRequestZhixing",
     lambda v: v.__setitem__("idempotency_key", "00000000-0000-1000-8000-00000000c010")),
    ("路由请求的学科不在枚举里", "ModelRouteRequest", "ModelRouteRequestZhixing", lambda v: v.__setitem__("subject", "daofa")),
    ("路由请求的任务类型不在枚举里", "ModelRouteRequest", "ModelRouteRequestZhixing", lambda v: v.__setitem__("task", "T0")),
    ("路由请求的消息角色是 tool", "ModelRouteRequest", "ModelRouteRequestZhixing", lambda v: v["messages"][0].__setitem__("role", "tool")),
    ("路由请求的 prompt 名带斜杠", "ModelRouteRequest", "ModelRouteRequestZhixing", lambda v: v.__setitem__("prompt_template", "zhixing/m3")),
    ("路由响应的模型版本写 routed", "ModelRouteResponse", "ModelRouteResponseZhixing", lambda v: v.__setitem__("model_version", "routed")),
    ("路由响应缺 model_mismatch", "ModelRouteResponse", "ModelRouteResponseZhixing", lambda v: v.pop("model_mismatch")),
    # 今日计划（冲刺 4-H，specs/unified-shell-sprint4 第 6 节；契约变更待签字）：响应里不能有关于学生的推断
    ("计划任务夹带掌握概率", "PlanDigest", "PlanDigestToday", lambda v: v["subjects"][0]["tasks"][0].__setitem__("p_mastery", 0.4)),
    ("计划任务夹带置信度", "PlanDigest", "PlanDigestToday", lambda v: v["subjects"][0]["tasks"][0].__setitem__("confidence", 0.8)),
    ("计划任务夹带证据 ID", "PlanDigest", "PlanDigestToday",
     lambda v: v["subjects"][0]["tasks"][0].__setitem__("evidence_ids", ["00000000-0000-4000-8000-00000000e401"])),
    ("计划学科夹带画像上下文", "PlanDigest", "PlanDigestToday", lambda v: v["subjects"][0].__setitem__("learner_context", [])),
    ("计划响应顶层夹带叙述文字", "PlanDigest", "PlanDigestToday", lambda v: v.__setitem__("narrative", "（合成）")),
    ("计划任务的理由写成句子", "PlanDigest", "PlanDigestToday",
     lambda v: v["subjects"][0]["tasks"][0].__setitem__("rationale_code", "该复习了")),
    ("计划任务的目标写成能力点", "PlanDigest", "PlanDigestToday",
     lambda v: v["subjects"][0]["tasks"][0].__setitem__("target_node_ids", ["aya:K-QE-0102"])),
    ("不可用的学科还带任务", "PlanDigest", "PlanDigestToday",
     lambda v: v["subjects"][1]["tasks"].append(dict(v["subjects"][0]["tasks"][0]))),
    ("计划任务类型不在枚举里", "PlanDigest", "PlanDigestToday", lambda v: v["subjects"][0]["tasks"][0].__setitem__("kind", "homework")),
    ("计划请求带英语（搁置）", "PlanQuery", "PlanQueryToday", lambda v: v["subjects"].append("echo")),
    ("计划请求自带掌握快照", "PlanQuery", "PlanQueryToday", lambda v: v.__setitem__("mastery", [])),
    ("计划请求的学科为空", "PlanQuery", "PlanQueryToday", lambda v: v.__setitem__("subjects", [])),
    ("计划请求的学生 ID 大写", "PlanQuery", "PlanQueryToday", lambda v: v.__setitem__("student_ref", v["student_ref"].upper())),
]


def _check_internal_v1_examples(errors: list[str]) -> None:
    import copy  # noqa: PLC0415
    sys.path.insert(0, str(HERE.parent / "python"))
    from starbuddy_contract.contract import internal_contract  # noqa: PLC0415
    c = internal_contract("1.0.0")
    examples = load(INTERNAL_SPECS[1])["components"]["examples"]
    schema_of = {"GradeIngestKeypad": "GradeIngest", "GradeSummaryLevelC": "GradeSummary",
                 "DiagnoseIngestCanvasC": "DiagnoseIngest", "DiagnoseSummaryLevelC": "DiagnoseSummary",
                 "ModelRouteRequestZhixing": "ModelRouteRequest", "ModelRouteResponseZhixing": "ModelRouteResponse",
                 "PlanQueryToday": "PlanQuery", "PlanDigestToday": "PlanDigest"}
    for name, schema in schema_of.items():
        errs = c.validate(schema, examples[name]["value"])
        if errs:
            errors.append(f"learning-internal.v1 示例 {name} 不符合 {schema}：{errs[:3]}")
    for label, schema, base, mutate in INTERNAL_V1_COUNTER:
        value = copy.deepcopy(examples[base]["value"])
        mutate(value)
        if not c.validate(schema, value):
            errors.append(f"learning-internal.v1 反例没有被拒绝：{label}")


def _check_internal_one(spec: Path, errors: list[str]) -> int:
    doc = load(spec)
    try:
        validate_openapi(doc, base_uri=spec.as_uri())
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{spec.name} OpenAPI 校验失败：{str(exc)[:500]}")
    refs = 0

    def walk(node):
        nonlocal refs
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and not ref.startswith("#"):
                refs += 1
                file, _, pointer = ref.partition("#")
                target = (spec.parent / file).resolve()
                if not target.exists():
                    errors.append(f"{spec.name}：$ref 指向的文件不存在 {file}")
                else:
                    cur = load(target)
                    for part in pointer.strip("/").split("/"):
                        if not isinstance(cur, dict) or part not in cur:
                            errors.append(f"{spec.name}：$ref 无法解析 {ref}")
                            break
                        cur = cur[part]
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(doc)
    return refs


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def main() -> int:
    if len(sys.argv) > 1:
        spec = Path(sys.argv[1])
        doc = load(spec)
        errors = run_checks(doc, spec)
    else:
        doc = load(DEFAULT_SPEC)
        v1 = load(V1_SPEC)
        errors = run_checks(doc, DEFAULT_SPEC) + [f"v1.0：{e}" for e in run_checks(v1, V1_SPEC)]
        try:
            n_breaking = check_breaking(doc, v1, errors)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"check_breaking 无法完成：{type(exc).__name__}: {exc}")
            n_breaking = 0
    internal_refs = check_internal(errors) if len(sys.argv) <= 1 else None
    service_summary = None
    if len(sys.argv) <= 1:
        # 知识服务与洞察服务 OpenAPI v0.9（specs/service-openapi-v0.9/spec.md）
        import check_services  # noqa: PLC0415

        service_docs = check_services.load_all()
        errors.extend(check_services.run_service_checks(service_docs))
        service_summary = check_services.summary(service_docs)
    if errors:
        print(f"contract-test: 失败（{len(errors)} 项）")
        for e in errors:
            print(f"  - {e}")
        return 1
    n_ex = len(doc["components"]["examples"])
    sys.path.insert(0, str(HERE.parent / "tests"))
    from counterexamples import CASES  # noqa: PLC0415
    from counterexamples_v1 import V1_CASES  # noqa: PLC0415
    print(f"contract-test: 通过（OpenAPI 3.1 合法；{n_ex} 个示例符合 schema 且证据引用闭合；"
          f"{len(CASES)} 个反例全部被拒绝（v1.0 另加 {len(V1_CASES)} 个）；六方法信封、计量字段、无约束文本扫描、JobStatus 绑定、示例无 PII"
          + (f"；learning-internal v0 与 v1 合法、{internal_refs} 个跨文件引用可解析、v1 信封与摘要 {len(INTERNAL_V1_COUNTER)} 个反例全部被拒绝" if internal_refs is not None else "")
          + (f"；{service_summary}" if service_summary else "")
          + (f"；v1.0 候选版合法、{len(v1['components']['examples'])} 个示例、{n_breaking} 条破坏性变更 v0.9 接受 / v1.0 拒绝"
             if len(sys.argv) <= 1 else "") + "）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
