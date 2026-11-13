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
不带参数时检查 v0.9、v1.0（2026-10-09 签字）与 v1.1 候选版三份（迁移期并存，specs/plugin-api-v1.0/spec.md），外加 v1.0 的破坏性变更反例，
以及 v1.1 只做增量（v1.0 的 schema、路径、示例逐项不变，只多 present_item；specs/present-item-contract/spec.md）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OPENAPI = HERE.parent / "openapi"
DEFAULT_SPEC = OPENAPI / "plugin-api.v0.9.yaml"
V1_SPEC = OPENAPI / "plugin-api.v1.0.yaml"
V11_SPEC = OPENAPI / "plugin-api.v1.1.yaml"  # PLT-12：v1.0 + 只读方法 present_item
SPECS = [DEFAULT_SPEC, V1_SPEC, V11_SPEC]

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
    "present_item": "PresentItemResponse",  # v1.1（PLT-12）
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
    # v1.1 present_item：题面本身是教研内容（受限富文本）。只有文字片段与图片说明两处；都必须限长，且另由
    # check_present_item 用探针核对它们拒收 HTML 标签与控制字符（规则：题面不能夹带 HTML）
    ("RichTextRun", "text"): {"PresentItemResponse"},
    ("RichImageRun", "alt"): {"PresentItemResponse"},
}
# 自由文本扫描的根：各方法的响应（取决于契约的 Method 枚举）+ 异步任务状态（失败时带 Problem）
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


def _ver(doc: dict) -> tuple[int, ...]:
    return tuple(int(x) for x in str(doc["info"]["version"]).split("."))


def _methods(doc: dict) -> list[str]:
    """契约声明的方法（Method 枚举）。v0.9 / v1.0 是六个学科方法，v1.1 多一个只读的 present_item。
    枚举缺失或被改坏时退回六个方法，由 check_v11_additive 报出。"""
    enum = _schemas(doc).get("Method", {}).get("enum")
    return [m for m in enum if m in RESPONSES] if isinstance(enum, list) and enum else list(METHODS)


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

    methods = _methods(doc)
    for m in methods:
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
        if m == "present_item":  # v1.1：题面与学生无关，用自己的上下文（不带 student_ref、usage_id）
            if ctx != "PresentItemContext":
                errors.append("present_item 的 context 必须是 PresentItemContext（不带 student_ref、usage_id）")
        elif m not in METERED and ctx != "RequestContext":
            errors.append(f"{m} 是非消耗类方法，context 应基于 RequestContext")

    ctx = schemas.get("RequestContext", {})
    if "idempotency_key" not in ctx.get("required", []):
        errors.append("RequestContext.idempotency_key 必须必填")
    metered = schemas.get("MeteredContext", {})
    if not any("usage_id" in s.get("required", []) for s in metered.get("allOf", [])):
        errors.append("MeteredContext 没有把 usage_id 设为必填")
    if "semantics" not in base.get("required", []):
        errors.append("PluginResultBase.semantics 必须必填（内容准入）")

    for name in [*(RESPONSES[m] for m in methods), "JobStatus"]:
        if name in schemas:
            errors.extend(_free_text_leaks(doc, name))
    extra_paths = sorted(p for p in doc.get("paths", {}) if p.lstrip("/") in RESPONSES and p.lstrip("/") not in methods)
    if extra_paths:
        errors.append(f"有方法路径不在 Method 枚举里（能力声明与覆盖率统计不到它）：{extra_paths}")

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
    for m in _methods(doc):
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
    from counterexamples_v11 import V11_CASES  # noqa: PLC0415

    registry, uri = _registry(doc, path)
    examples = doc["components"]["examples"]
    # v1.0 的反例对 v1.1 照样成立（v1.1 只做增量）；v1.1 另有 present_item 的反例
    cases = [*CASES, *(V1_CASES if _ver(doc) >= (1, 0, 0) else []), *(V11_CASES if _ver(doc) >= (1, 1, 0) else [])]
    for case in cases:
        value = case.build(examples)
        if not isinstance(value, dict):  # mutate 误返回了被删掉的值（dict.pop）：反例变成了别的东西，证明不了规则
            errors.append(f"反例构造错了（实例不是对象）：{case.name}")
            continue
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
    if "PresentItemContext" in schemas:  # v1.1
        found["PresentItemContext"] = schemas["PresentItemContext"].get("properties", {}).get("contract_version", {}).get("const")
    for where, v in found.items():
        if str(v) != version:
            errors.append(f"{where}.contract_version（{v}）与 info.version（{version}）不一致")
    # 能力声明必须逐一声明 Method 枚举里的每个方法（一致性测试按 Method 枚举核对能力声明）
    declared = schemas["Capabilities"]["properties"]["methods"]
    enum = set(schemas.get("Method", {}).get("enum", []))
    if set(declared.get("required", [])) != enum or set(declared.get("properties", {})) != enum:
        errors.append(f"Capabilities.methods 的必填与属性必须正好是 Method 枚举（{sorted(enum)}）")


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
    """v1.0 起的结构要求：衔接字段都在且不是必填（迁移期平台未必能填）；新增错误码在枚举里；预测不在报告里。
    v1.1 以 v1.0 为底，同样适用。"""
    if _ver(doc) < (1, 0, 0):
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


# ---------- v1.1：present_item（PLT-12，specs/present-item-contract/spec.md） ----------
PRESENT_SCHEMAS = {"PresentItemContext", "PresentItemRequest", "PresentItemResponse", "PresentedItem", "PresentedOption", "AnswerFormat", "LicenseState",
                   "RichText", "RichParagraph", "RichInline", "RichTextRun", "RichMathRun", "RichImageRun"}
# 受限富文本字段的探针：每个 pattern 必须拒收 reject、接受 accept（防止有人把 pattern 放宽成“任意文字”而扫描看不出来）
# 双向文本控制符、零宽与方向标记、行/段分隔符、BOM、C1 控制字符：都能让显示内容与实际内容不一致（审查建议 4）
INVISIBLE_PROBES = ["a\u202eb", "a\u202ab", "a\u2066b", "a\u2069b", "a\u200bb", "a\u200fb", "a\u2028b",
                    "a\u2029b", "a\ufeffb", "a\u0080b", "a\u0085b", "a\u009fb"]
PRESENT_PROBES = {
    ("RichTextRun", "text"): (["<script>alert(1)</script>", "x<y", "<img src=x onerror=alert(1)>", "a\u0000b", "a\u001bb",
                               *INVISIBLE_PROBES],
                              ["（合成）把下面的式子化简：x = 1", "第 2 步：a+b", "温度 30°C，é"]),
    ("RichImageRun", "alt"): (["<b>图</b>", "a\u0007b", *INVISIBLE_PROBES], ["（合成）一个直角三角形"]),
    ("RichMathRun", "latex"): (["\\href{https://x.invalid}{y}", "\\url{https://x.invalid}", "\\includegraphics{a.png}",
                                "\\htmlClass{a}{b}", "\\def\\x{1}", "\\newcommand{\\a}{b}", "x^{\\input{f}}", "解：x=1"],
                               ["\\frac{1}{2}", "\\left(x-1\\right)^2", "x<3", "\\sqrt{2}\\times 3", "\\text{cm}"]),
    ("RichImageRun", "image_ref"): (["https://example.invalid/a.png", "javascript:alert(1)", "data:image/png;base64,AAAA",
                                     "objstore://../etc/passwd x"], ["objstore://synthetic/aya/fig-01.png"]),
}


def _local_refs(node, schemas: dict, seen: set[str]):
    """深度遍历 schema（跟着本文件的 $ref 走），产出每个节点。"""
    if isinstance(node, list):
        for x in node:
            yield from _local_refs(x, schemas, seen)
        return
    if not isinstance(node, dict):
        return
    yield node
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
        name = _refname(ref)
        if name not in seen:
            seen.add(name)
            yield from _local_refs(schemas.get(name, {}), schemas, seen)
    for k, v in node.items():
        if k not in ("$ref", "description", "examples", "enum", "const", "pattern"):
            yield from _local_refs(v, schemas, seen)


def check_present_item(doc: dict, errors: list[str]) -> None:
    """v1.1 起：present_item 的结构要求。
    1. 题面 schema 树（含引用）里没有答案类字段名（ANSWER_FIELD_NAMES，与学习服务、一致性测试同一份）；
    2. 题面结果的每个对象都 additionalProperties: false，每个数组都有 maxItems；
    3. 受限富文本字段的 pattern 用探针核对：拒收 HTML 标签、控制字符、危险 LaTeX 命令、公网 URL；
    4. 请求必须带 item_ref 与 content_version；响应不得带证据。"""
    if _ver(doc) < (1, 1, 0):
        return
    sys.path.insert(0, str(HERE.parent / "python"))
    from starbuddy_contract.present import ANSWER_FIELD_NAMES  # noqa: PLC0415

    schemas = _schemas(doc)
    missing = sorted(PRESENT_SCHEMAS - set(schemas))
    if missing:
        errors.append(f"v1.1 缺少 present_item 的 schema：{missing}")
        return
    for node in _local_refs(schemas["PresentItemResponse"], schemas, {"PresentItemResponse"}):
        for k in node.get("properties", {}) if isinstance(node.get("properties"), dict) else {}:
            if str(k).lower() in ANSWER_FIELD_NAMES:
                errors.append(f"present_item 题面里出现答案类字段 {k}（题面不得含答案、解析、采分点、Rubric）")
        if node.get("patternProperties") or node.get("unevaluatedProperties") is True:
            errors.append("present_item 题面不得用 patternProperties / unevaluatedProperties: true 放开字段")
    for node in _local_refs(schemas["PresentedItem"], schemas, {"PresentedItem"}):
        if node.get("type") == "object" and node.get("additionalProperties") is not False:
            errors.append("present_item 题面结果的对象必须 additionalProperties: false")
        if node.get("type") == "array" and "maxItems" not in node:
            errors.append("present_item 题面结果的数组必须有 maxItems")
    for (schema, prop), (reject, accept) in PRESENT_PROBES.items():
        node = schemas[schema].get("properties", {}).get(prop, {})
        pat = node.get("pattern")
        if not isinstance(pat, str):
            errors.append(f"{schema}.{prop} 没有 pattern（受限富文本必须约束字符）")
            continue
        try:
            rx = re.compile(pat)
        except re.error as exc:
            errors.append(f"{schema}.{prop} 的 pattern 不能编译：{exc}")
            continue
        for probe in reject:
            if rx.search(probe):
                errors.append(f"{schema}.{prop} 的 pattern 接受了不该接受的内容（探针 {probe[:24]!r}）")
        for probe in accept:
            if not rx.search(probe):
                errors.append(f"{schema}.{prop} 的 pattern 拒收了正常内容（探针 {probe[:24]!r}）")
    req = schemas["PresentItemRequest"]
    if not {"context", "item_ref", "content_version"} <= set(req.get("required", [])) or req.get("additionalProperties") is not False:
        errors.append("PresentItemRequest 必须要求 context、item_ref、content_version，且不放开多余字段")
    pctx = schemas["PresentItemContext"]
    pprops = pctx.get("properties", {}) if isinstance(pctx.get("properties"), dict) else {}
    if pctx.get("additionalProperties") is not False:
        errors.append("PresentItemContext 必须 additionalProperties: false（不能夹带 student_ref、usage_id）")
    for banned in ("student_ref", "usage_id"):
        if banned in pprops or banned in pctx.get("required", []):
            errors.append(f"PresentItemContext 不得有 {banned}（题面与学生无关、不计量）")
    if not {"request_id", "subject", "data_class", "contract_version"} <= set(pctx.get("required", [])):
        errors.append("PresentItemContext 必须要求 request_id、subject、data_class、contract_version")
    if "idempotency_key" in pctx.get("required", []):
        errors.append("PresentItemContext.idempotency_key 应为可选（只读请求不进幂等缓存）")
    resp_parts = schemas["PresentItemResponse"].get("allOf", [])
    extra = resp_parts[1] if len(resp_parts) > 1 else {}
    if extra.get("properties", {}).get("evidence_ids", {}).get("maxItems") != 0 or extra.get("not") != {"required": ["evidence"]}:
        errors.append("PresentItemResponse 不得产生证据（evidence_ids 为空、不带 evidence）")


V11_VERSION_CONSTS = (("RequestContext", "contract_version"), ("PluginResultBase", "contract_version"), ("Capabilities", "contract_version"))


def _bump_versions(value, old: str, new: str):
    """把实例里的 contract_version 从 old 换成 new（只改这个键），用于逐项对照两版示例。"""
    if isinstance(value, dict):
        return {k: (new if k == "contract_version" and v == old else _bump_versions(v, old, new)) for k, v in value.items()}
    if isinstance(value, list):
        return [_bump_versions(v, old, new) for v in value]
    return value


def check_v11_additive(v1: dict, v11: dict, errors: list[str]) -> None:
    """v1.1 只做增量（spec 范围第 1 条：不改 v1.0 正在表决的 61 项）：
    v1.0 的每个 schema、路径、响应、示例在 v1.1 里逐字不变；只允许三处契约版本常量改成 1.1.0、Method 枚举与能力声明
    多一个 present_item、示例里的 contract_version 改成 1.1.0、能力声明示例多一个 present_item；新增的只能是 present_item 的
    路径与 schema、示例。"""
    import copy  # noqa: PLC0415

    if str(v11["info"]["version"]) != "1.1.0":
        errors.append(f"v1.1 的 info.version 应为 1.1.0，实为 {v11['info']['version']}")
    s1, s11 = copy.deepcopy(_schemas(v1)), copy.deepcopy(_schemas(v11))
    for name, prop in V11_VERSION_CONSTS:
        for s in (s1, s11):
            s.get(name, {}).get("properties", {}).get(prop, {}).pop("const", None)
    m1, m11 = s1.get("Method", {}).get("enum", []), s11.get("Method", {}).get("enum", [])
    if m11 != [*m1, "present_item"]:
        errors.append(f"v1.1 的 Method 枚举必须是 v1.0 的六个方法加 present_item，实为 {m11}")
    s11.get("Method", {})["enum"] = list(m1)
    caps = s11.get("Capabilities", {}).get("properties", {}).get("methods", {})
    if "present_item" not in caps.get("required", []) or "present_item" not in caps.get("properties", {}):
        errors.append("v1.1 的能力声明必须声明 present_item")
    caps.get("properties", {}).pop("present_item", None)
    if isinstance(caps.get("required"), list):
        caps["required"] = [m for m in caps["required"] if m != "present_item"]
    caps.pop("description", None)
    s1.get("Capabilities", {}).get("properties", {}).get("methods", {}).pop("description", None)
    for name, sch in s1.items():
        if name not in s11:
            errors.append(f"v1.1 删掉了 v1.0 的 schema {name}")
        elif s11[name] != sch:
            errors.append(f"v1.1 改动了 v1.0 的 schema {name}（只允许增量）")
    extra = set(s11) - set(s1)
    if extra - PRESENT_SCHEMAS:
        errors.append(f"v1.1 新增了 present_item 之外的 schema：{sorted(extra - PRESENT_SCHEMAS)}")
    p1, p11 = v1.get("paths", {}), v11.get("paths", {})
    for path, item in p1.items():
        if p11.get(path) != item:
            errors.append(f"v1.1 改动或删掉了 v1.0 的路径 {path}")
    if set(p11) - set(p1) != {"/present_item"}:
        errors.append(f"v1.1 新增的路径只能是 /present_item，实为 {sorted(set(p11) - set(p1))}")
    for key in ("responses", "securitySchemes"):
        if v1["components"].get(key) != v11["components"].get(key):
            errors.append(f"v1.1 改动了 v1.0 的 components.{key}")
    if v1.get("security") != v11.get("security") or v1.get("servers") != v11.get("servers"):
        errors.append("v1.1 改动了 v1.0 的 security / servers")
    e1, e11 = v1["components"].get("examples", {}), copy.deepcopy(v11["components"].get("examples", {}))
    cap_ex = e11.get("CapabilitiesAya", {}).get("value", {}).get("methods", {})
    cap_ex.pop("present_item", None)
    for name, ex in e1.items():
        if name not in e11:
            errors.append(f"v1.1 删掉了 v1.0 的示例 {name}")
        elif e11[name] != _bump_versions(ex, "1.0.0", "1.1.0"):
            errors.append(f"v1.1 改动了 v1.0 的示例 {name}（只允许把 contract_version 换成 1.1.0）")
    if any(not n.startswith("PresentItem") for n in set(e11) - set(e1)):
        errors.append(f"v1.1 新增的示例只能是 present_item 的：{sorted(set(e11) - set(e1))}")


def run_checks(doc: dict, path: Path = DEFAULT_SPEC) -> list[str]:
    errors: list[str] = []
    try:
        validate_openapi(doc, base_uri=path.resolve().as_uri())
    except Exception as exc:  # noqa: BLE001 - 打印校验器的原始信息
        errors.append(f"OpenAPI 3.1 校验失败：{str(exc)[:500]}")
    for fn in (check_structure, check_versions, check_v1_rules, check_present_item):
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
    # 1.1.0 掌握读取（KGE-04 第二步 PR 3）：形状照搬知识服务 v0.9（schema 名带 ks: 的按知识服务契约校验）
    ("掌握读取的调用方是 bff", "ks:MasteryGetRequest", "MasteryGetRequestOrchestrator", lambda v: v["context"].__setitem__("caller", "bff")),
    ("掌握读取缺学生", "ks:MasteryGetRequest", "MasteryGetRequestOrchestrator", lambda v: v["context"].pop("student_ref")),
    ("掌握读取的节点为空", "ks:MasteryGetRequest", "MasteryGetRequestOrchestrator", lambda v: v.__setitem__("node_ids", [])),
    ("掌握读取夹带多余字段", "ks:MasteryGetRequest", "MasteryGetRequestOrchestrator", lambda v: v.__setitem__("include_claims", True)),
    ("掌握响应缺 confidence", "ks:MasteryGetResponse", "MasteryGetResponseExecute", lambda v: v.pop("confidence")),
    ("掌握响应缺 evidence_ids", "ks:MasteryGetResponse", "MasteryGetResponseExecute", lambda v: v.pop("evidence_ids")),
    ("掌握响应缺 source_version", "ks:MasteryGetResponse", "MasteryGetResponseExecute", lambda v: v.pop("source_version")),
    ("掌握响应缺 model_version", "ks:MasteryGetResponse", "MasteryGetResponseExecute", lambda v: v.pop("model_version")),
    ("掌握响应缺 human_review_required", "ks:MasteryGetResponse", "MasteryGetResponseExecute", lambda v: v.pop("human_review_required")),
    ("掌握响应要复核却没有原因", "ks:MasteryGetResponse", "MasteryGetResponseExecute", lambda v: v.__setitem__("human_review_required", True)),
    ("掌握响应的模型版本写 routed", "ks:MasteryGetResponse", "MasteryGetResponseExecute", lambda v: v.__setitem__("model_version", "routed")),
    ("掌握状态缺 star_lit", "ks:MasteryGetResponse", "MasteryGetResponseExecute", lambda v: v["result"]["states"][1].pop("star_lit")),
    ("掌握门槛写 lit", "ks:MasteryGetResponse", "MasteryGetResponseExecute", lambda v: v["result"]["states"][1].__setitem__("gate", "lit")),
    ("掌握状态夹带门槛条件", "ks:MasteryGetResponse", "MasteryGetResponseExecute",
     lambda v: v["result"]["states"][1].__setitem__("conditions", [{"code": "execute.families_min"}])),
    ("掌握响应信封外夹带叙述", "ks:MasteryGetResponse", "MasteryGetResponseExecute", lambda v: v.__setitem__("narrative", "（合成）")),
    ("证据链的提示等级是 6", "ks:MasteryExplainResponse", "MasteryExplainResponseExecute",
     lambda v: v["result"]["chain"][0].__setitem__("hint_level", 6)),
    ("证据链的权重大于 1", "ks:MasteryExplainResponse", "MasteryExplainResponseExecute",
     lambda v: v["result"]["chain"][0].__setitem__("weight", 1.2)),
    ("证据链的权重为负", "ks:MasteryExplainResponse", "MasteryExplainResponseExecute",
     lambda v: v["result"]["chain"][0].__setitem__("weight", -0.7)),
    ("证据链有 C 级", "ks:MasteryExplainResponse", "MasteryExplainResponseExecute",
     lambda v: v["result"]["chain"][0].__setitem__("evidence_level", "C")),
    ("证据链缺权重", "ks:MasteryExplainResponse", "MasteryExplainResponseExecute", lambda v: v["result"]["chain"][0].pop("weight")),
    ("证据链夹带不计入原因", "ks:MasteryExplainResponse", "MasteryExplainResponseExecute",
     lambda v: v["result"]["chain"][0].__setitem__("reasons", ["pending_review"])),
    ("证据链结果夹带门槛条件", "ks:MasteryExplainResponse", "MasteryExplainResponseExecute",
     lambda v: v["result"].__setitem__("conditions", [])),
    ("证据链缺 confidence", "ks:MasteryExplainResponse", "MasteryExplainResponseExecute", lambda v: v.pop("confidence")),
    # 1.1.0 验证任务：学生只由洞察服务从异议记录取出；不返回题目
    ("验证请求缺异议引用", "VerificationRequest", "VerificationRequestIKnow", lambda v: v.pop("dispute_ref")),
    ("验证请求缺学生", "VerificationRequest", "VerificationRequestIKnow", lambda v: v.pop("student_ref")),
    ("验证请求的学生 ID 大写", "VerificationRequest", "VerificationRequestIKnow", lambda v: v.__setitem__("student_ref", v["student_ref"].upper())),
    ("验证请求的原因是复测轮", "VerificationRequest", "VerificationRequestIKnow", lambda v: v.__setitem__("reason", "scheduled")),
    ("验证请求要 6 道题", "VerificationRequest", "VerificationRequestIKnow", lambda v: v.__setitem__("item_count", 6)),
    ("验证请求要 0 道题", "VerificationRequest", "VerificationRequestIKnow", lambda v: v.__setitem__("item_count", 0)),
    ("验证请求的学科是英语（搁置）", "VerificationRequest", "VerificationRequestIKnow", lambda v: v.__setitem__("subject", "echo")),
    ("验证请求的节点写成能力点", "VerificationRequest", "VerificationRequestIKnow", lambda v: v.__setitem__("node_id", "aya:K-QE-0102")),
    ("验证请求的层次不在课标四级", "VerificationRequest", "VerificationRequestIKnow", lambda v: v.__setitem__("cognitive_level", "master")),
    ("验证请求夹带学生姓名", "VerificationRequest", "VerificationRequestIKnow", lambda v: v.__setitem__("student_name", "（合成）")),
    ("验证请求自带掌握门槛", "VerificationRequest", "VerificationRequestIKnow", lambda v: v.__setitem__("gate", "transfer")),
    ("验证任务带题目引用", "VerificationTask", "VerificationTaskCreated", lambda v: v.__setitem__("item_ref", "aya:synthetic-0301-08")),
    ("验证任务带题干", "VerificationTask", "VerificationTaskCreated", lambda v: v.__setitem__("stem", "（合成）")),
    ("验证任务状态不在枚举里", "VerificationTask", "VerificationTaskCreated", lambda v: v.__setitem__("status", "done")),
    ("验证任务缺任务 ID", "VerificationTask", "VerificationTaskCreated", lambda v: v.pop("task_id")),
    ("验证任务缺到期时间", "VerificationTask", "VerificationTaskCreated", lambda v: v.pop("due_at")),
    # 1.1.0 撤销验证任务（洞察服务事务回滚时的补偿）
    ("撤销请求缺学生", "VerificationCancelRequest", "VerificationCancelRequestRollback", lambda v: v.pop("student_ref")),
    ("撤销请求缺任务 ID", "VerificationCancelRequest", "VerificationCancelRequestRollback", lambda v: v.pop("task_id")),
    ("撤销请求的学生 ID 大写", "VerificationCancelRequest", "VerificationCancelRequestRollback",
     lambda v: v.__setitem__("student_ref", v["student_ref"].upper())),
    ("撤销请求夹带原因（只撤自述验证任务）", "VerificationCancelRequest", "VerificationCancelRequestRollback",
     lambda v: v.__setitem__("reason", "scheduled")),
    ("撤销结果状态不在枚举里", "VerificationCancelResult", "VerificationCancelResultCancelled", lambda v: v.__setitem__("status", "deleted")),
    ("撤销结果缺 cancelled", "VerificationCancelResult", "VerificationCancelResultCancelled", lambda v: v.pop("cancelled")),
    ("撤销结果夹带掌握门槛", "VerificationCancelResult", "VerificationCancelResultCancelled", lambda v: v.__setitem__("gate", "stable")),
    # 1.1.0 草稿内追加（PR 4）：学情档案页
    ("档案页 ensure 的触发点不在两个之内（作答触发编译）", "ProfilePageEnsureRequest", "ProfilePageEnsureSessionStart",
     lambda v: v.__setitem__("trigger", "attempt")),
    ("档案页 ensure 缺触发点", "ProfilePageEnsureRequest", "ProfilePageEnsureSessionStart", lambda v: v.pop("trigger")),
    ("档案页 ensure 的学生 ID 大写", "ProfilePageEnsureRequest", "ProfilePageEnsureSessionStart",
     lambda v: v.__setitem__("student_ref", v["student_ref"].upper())),
    ("档案页 ensure 的学科是英语（搁置）", "ProfilePageEnsureRequest", "ProfilePageEnsureSessionStart", lambda v: v.__setitem__("subject", "echo")),
    ("档案页 ensure 夹带模型（衔接语接模型）", "ProfilePageEnsureRequest", "ProfilePageEnsureSessionStart",
     lambda v: v.__setitem__("model", "fake-a/fake-reasoner")),
    ("档案页 ensure 结果夹带页面文字", "ProfilePageEnsureResult", "ProfilePageEnsureCompiled", lambda v: v.__setitem__("sections", [])),
    ("档案页 ensure 结果的编码不在枚举里", "ProfilePageEnsureResult", "ProfilePageEnsureCompiled", lambda v: v.__setitem__("outcome", "queued")),
    ("档案页读取请求夹带受众（学生、家长视图不走这里）", "ProfilePageReadRequest", "ProfilePageReadBasic",
     lambda v: v.__setitem__("audience", "student")),
    ("档案页陈述引用待验证判断（规则 11）", "ProfilePage", "ProfilePageCompiled",
     lambda v: v["sections"][1]["statements"][0].__setitem__("status", "to_verify")),
    ("档案页陈述没有 claim_id", "ProfilePage", "ProfilePageCompiled", lambda v: v["sections"][1]["statements"][0].pop("claim_id")),
    ("档案页待办带文字", "ProfilePage", "ProfilePageCompiled", lambda v: v["sections"][2]["todo"][0].__setitem__("text", "（合成）")),
    ("档案页衔接语含数字", "ProfilePage", "ProfilePageCompiled", lambda v: v["sections"][1].__setitem__("connective", "这周做对了3题")),
    ("档案页衔接语超过 60 字", "ProfilePage", "ProfilePageCompiled", lambda v: v["sections"][1].__setitem__("connective", "长" * 61)),
    ("档案页节不在固定六节里", "ProfilePage", "ProfilePageCompiled", lambda v: v["sections"][0].__setitem__("key", "personality")),
    ("档案页少一节", "ProfilePage", "ProfilePageCompiled", lambda v: v["sections"].pop()),
    ("档案页节里夹带总结", "ProfilePage", "ProfilePageCompiled", lambda v: v["sections"][1].__setitem__("summary", "（合成）")),
    ("档案页夹带模型版本", "ProfilePage", "ProfilePageCompiled", lambda v: v.__setitem__("model_version", "model:fake-a/x@1")),
    ("纯模板版本却带页 ID", "ProfilePage", "ProfilePageCompiled", lambda v: v.__setitem__("variant", "template_only")),
    ("已编译版本缺编译时间", "ProfilePage", "ProfilePageCompiled", lambda v: v.__setitem__("compiled_at", None)),
    # 1.1.0 草稿内追加（H5 星图 v1 PR 1）：星图节点星态只有四个字段，不含推断数值与各层次明细
    ("星图请求缺调用方", "StarMapNodesRequest", "StarMapNodesRequestBff", lambda v: v.pop("caller")),
    ("星图请求的调用方不是服务身份", "StarMapNodesRequest", "StarMapNodesRequestBff", lambda v: v.__setitem__("caller", "student")),
    ("星图请求的学生 ID 大写", "StarMapNodesRequest", "StarMapNodesRequestBff", lambda v: v.__setitem__("student_ref", v["student_ref"].upper())),
    ("星图请求的学科是英语（搁置）", "StarMapNodesRequest", "StarMapNodesRequestBff", lambda v: v.__setitem__("subject", "echo")),
    ("星图请求自带节点列表", "StarMapNodesRequest", "StarMapNodesRequestBff", lambda v: v.__setitem__("node_ids", ["MATH.SYN.FUNC.AXIS"])),
    ("星图节点夹带掌握概率", "StarMapNodes", "StarMapNodesOk", lambda v: v["nodes"][-1].__setitem__("p_mastery", 0.9)),
    ("星图节点夹带置信度", "StarMapNodes", "StarMapNodesOk", lambda v: v["nodes"][-1].__setitem__("confidence", 0.8)),
    ("星图节点夹带证据 ID", "StarMapNodes", "StarMapNodesOk",
     lambda v: v["nodes"][-1].__setitem__("evidence_ids", ["00000000-0000-4000-8000-00000000e801"])),
    ("星图节点夹带各层次明细", "StarMapNodes", "StarMapNodesOk",
     lambda v: v["nodes"][-1].__setitem__("levels", [{"cognitive_level": "apply", "gate": "transfer"}])),
    ("星图节点夹带层次", "StarMapNodes", "StarMapNodesOk", lambda v: v["nodes"][-1].__setitem__("cognitive_level", "apply")),
    ("星图响应顶层夹带置信度", "StarMapNodes", "StarMapNodesOk", lambda v: v.__setitem__("confidence", 0.8)),
    ("星图响应顶层夹带证据 ID", "StarMapNodes", "StarMapNodesOk", lambda v: v.__setitem__("evidence_ids", [])),
    ("星图节点缺 basis_id", "StarMapNodes", "StarMapNodesOk", lambda v: v["nodes"][-2].pop("basis_id")),
    ("星图节点状态写 lit", "StarMapNodes", "StarMapNodesOk", lambda v: v["nodes"][-1].__setitem__("state", "lit")),
    ("没有掌握记录的星不是 unseen", "StarMapNodes", "StarMapNodesOk", lambda v: v["nodes"][-2].__setitem__("basis_id", None)),
    ("星态到迁移却没亮", "StarMapNodes", "StarMapNodesOk", lambda v: v["nodes"][-1].__setitem__("star_lit", False)),
    ("星态只到执行却亮了", "StarMapNodes", "StarMapNodesOk", lambda v: v["nodes"][-2].__setitem__("star_lit", True)),
    ("星图节点写成能力点", "StarMapNodes", "StarMapNodesOk", lambda v: v["nodes"][0].__setitem__("node_id", "aya:K-QE-0102")),
    ("无地图却带星", "StarMapNodes", "StarMapNodesNoMap",
     lambda v: v["nodes"].append({"node_id": "CHN.SYN.T1.N1", "state": "unseen", "star_lit": False, "basis_id": None})),
    ("无地图却带版本号", "StarMapNodes", "StarMapNodesNoMap", lambda v: v.__setitem__("map_version", "1.0.0")),
    ("有地图却没有版本号", "StarMapNodes", "StarMapNodesOk", lambda v: v.__setitem__("map_version", None)),
    ("星图状态写 unavailable（不可用走 409）", "StarMapNodes", "StarMapNodesNoMap", lambda v: v.__setitem__("status", "unavailable")),
]

# 1.1.0 路径与引用的结构要求（KGE-04 第二步 PR 3）：掌握读取的请求与响应必须直接引用知识服务 v0.9 的 schema（形状照搬，不另写一份）；
# 掌握读取只接受编排层令牌；验证任务与撤销只接受洞察服务令牌；这两种令牌不得出现在别的路径上
KS = "./knowledge-service.v0.9.yaml#/components/schemas/"
INTERNAL_V1_REFS = {
    "/internal/v1/mastery/get": (KS + "MasteryGetRequest", KS + "MasteryGetResponse"),
    "/internal/v1/mastery/explain": (KS + "MasteryExplainRequest", KS + "MasteryExplainResponse"),
    "/internal/v1/verification": ("#/components/schemas/VerificationRequest", "#/components/schemas/VerificationTask"),
    "/internal/v1/verification/cancel": ("#/components/schemas/VerificationCancelRequest", "#/components/schemas/VerificationCancelResult"),
    # PR 4：学情档案页（仍在未发布的 1.1.0 草稿内追加）
    "/internal/v1/profile-page/ensure": ("#/components/schemas/ProfilePageEnsureRequest", "#/components/schemas/ProfilePageEnsureResult"),
    "/internal/v1/profile-page/read": ("#/components/schemas/ProfilePageReadRequest", "#/components/schemas/ProfilePage"),
    # H5 星图 v1 PR 1（仍在未发布的 1.1.0 草稿内追加）
    "/internal/v1/star-map/nodes": ("#/components/schemas/StarMapNodesRequest", "#/components/schemas/StarMapNodes"),
}
INTERNAL_V1_SECURITY = {"/internal/v1/verification": [{"insightServiceToken": []}],
                        "/internal/v1/verification/cancel": [{"insightServiceToken": []}],
                        "/internal/v1/mastery/get": [{"orchestratorServiceToken": []}],
                        "/internal/v1/mastery/explain": [{"orchestratorServiceToken": []}],
                        # ensure：编排层（会话开始）或洞察服务（周报）二选一；read：只给编排层（Agent 完整版含待办）
                        "/internal/v1/profile-page/ensure": [{"orchestratorServiceToken": []}, {"insightServiceToken": []}],
                        "/internal/v1/profile-page/read": [{"orchestratorServiceToken": []}],
                        # 星图节点：只给 BFF（显式写出，不靠顶层缺省）
                        "/internal/v1/star-map/nodes": [{"bffServiceToken": []}]}
# 星图节点响应里不得出现的字段（H5 spec 第 2 节）：推断数值、证据与各层次明细只给编排层（mastery/get、explain）
STAR_MAP_FORBIDDEN = frozenset({"p_mastery", "confidence", "evidence_ids", "cognitive_level", "levels", "gates", "states",
                                "next_review_at", "next_retest_at", "conditions", "chain"})
# 注意：升 learning-internal.v1 的版本时同步改这里（以及 _check_internal_v1_structure 里写死的版本号）
INTERNAL_V1_VERSION = "1.1.0"


def _validate_internal(c, schema: str, value) -> list[str]:
    """schema 名带 ks: 的按知识服务契约里的同名 schema 校验（1.1.0 掌握读取照搬它）；其余按内部契约。"""
    if not schema.startswith("ks:"):
        return c.validate(schema, value)
    from jsonschema import Draft202012Validator, FormatChecker  # noqa: PLC0415
    target = (HERE.parent / "openapi" / "knowledge-service.v0.9.yaml").resolve().as_uri()
    v = Draft202012Validator({"$ref": f"{target}#/components/schemas/{schema[3:]}"}, registry=c.registry, format_checker=FormatChecker())
    return sorted({"/" + "/".join(str(p) for p in e.absolute_path) + f" {e.validator}" for e in v.iter_errors(value)})


def _check_internal_v1_structure(doc: dict, errors: list[str]) -> None:
    if doc["info"]["version"] != INTERNAL_V1_VERSION:  # 升版时同步改 INTERNAL_V1_VERSION
        errors.append(f"learning-internal.v1 版本应为 {INTERNAL_V1_VERSION}（KGE-04 第二步 PR 3），实为 {doc['info']['version']}")
    for path, (req, resp) in INTERNAL_V1_REFS.items():
        op = doc.get("paths", {}).get(path, {}).get("post")
        if op is None:
            errors.append(f"learning-internal.v1 缺少 POST {path}")
            continue
        got_req = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {}).get("$ref")
        got_resp = op.get("responses", {}).get("200", {}).get("content", {}).get("application/json", {}).get("schema", {}).get("$ref")
        if got_req != req or got_resp != resp:
            errors.append(f"learning-internal.v1 {path} 的请求 / 响应必须是 {req} / {resp}（实为 {got_req} / {got_resp}）")
        want_sec = INTERNAL_V1_SECURITY.get(path)
        if op.get("security") != want_sec:
            errors.append(f"learning-internal.v1 {path} 的鉴权应为 {want_sec or '服务令牌（顶层 security）'}，实为 {op.get('security')}")
    schemes = doc.get("components", {}).get("securitySchemes", {})
    for name in ("insightServiceToken", "orchestratorServiceToken"):
        if name not in schemes:
            errors.append(f"learning-internal.v1 缺少 {name}")
    # 专用令牌只用于各自的路径：其他路径不得接受它们
    owned = {"insightServiceToken": {"/internal/v1/verification", "/internal/v1/verification/cancel", "/internal/v1/profile-page/ensure"},
             "orchestratorServiceToken": {"/internal/v1/mastery/get", "/internal/v1/mastery/explain", "/internal/v1/profile-page/ensure",
                                          "/internal/v1/profile-page/read"}}
    # 档案页结构要求（PR 4）：陈述只能是成立或假设、待办只有引用（没有文字）、正好六节、衔接语不含数字
    sch = doc.get("components", {}).get("schemas", {})
    st = sch.get("ProfileStatement", {}).get("properties", {}).get("status", {}).get("enum")
    if st != ["hypothesis", "established"]:
        errors.append(f"learning-internal.v1 ProfileStatement.status 只能是 hypothesis / established（规则 11），实为 {st}")
    if sch.get("ClaimRef", {}).get("additionalProperties") is not False or "text" in sch.get("ClaimRef", {}).get("properties", {}):
        errors.append("learning-internal.v1 ClaimRef（待办）不得带文字或放开多余字段")
    for path, ops in doc.get("paths", {}).items():
        for op in ops.values():
            for name, paths in owned.items():
                if path not in paths and any(name in sec for sec in op.get("security", []) or []):
                    errors.append(f"learning-internal.v1 {path} 不得接受 {name}")
    _check_star_map_schema(sch, errors)


def _check_star_map_schema(sch: dict, errors: list[str]) -> None:
    """星图节点（H5 PR 1）：响应 schema 里（含引用到的本文件 schema）不得出现推断数值、证据与各层次明细的字段，
    每个对象都必须 additionalProperties: false；请求必须带调用方。"""
    seen: set[str] = set()

    def visit(node, trail: str) -> None:
        if isinstance(node, list):
            for i, x in enumerate(node):
                visit(x, f"{trail}[{i}]")
            return
        if not isinstance(node, dict):
            return
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            name = ref.rsplit("/", 1)[1]
            if name not in seen:
                seen.add(name)
                visit(sch.get(name, {}), name)
        props = node.get("properties")
        if isinstance(props, dict):
            for k in props:
                if k in STAR_MAP_FORBIDDEN:
                    errors.append(f"learning-internal.v1 {trail} 不得含 {k}（星图只给星态）")
        if node.get("type") == "object" and node.get("additionalProperties") is not False:
            errors.append(f"learning-internal.v1 {trail} 必须 additionalProperties: false（星图不得夹带推断）")
        if "patternProperties" in node or "unevaluatedProperties" in node and node["unevaluatedProperties"] is not False:
            errors.append(f"learning-internal.v1 {trail} 不得放开属性")
        for k, v in node.items():
            if k != "$ref":
                visit(v, f"{trail}.{k}")

    if "StarMapNodes" not in sch or "StarMapNode" not in sch:
        errors.append("learning-internal.v1 缺少 StarMapNodes / StarMapNode")
        return
    seen.add("StarMapNodes")
    visit(sch["StarMapNodes"], "StarMapNodes")
    if "StarMapNode" not in seen:
        errors.append("learning-internal.v1 StarMapNodes.nodes 必须引用 StarMapNode")
    req = sch.get("StarMapNodesRequest", {})
    if "caller" not in req.get("required", []) or req.get("additionalProperties") is not False:
        errors.append("learning-internal.v1 StarMapNodesRequest 必须要求 caller 且不放开多余字段")


def _check_internal_v1_examples(errors: list[str], doc: dict | None = None) -> None:
    """doc 为空时读仓库里的 learning-internal.v1.yaml；变异测试传入改坏后的文档。"""
    import copy  # noqa: PLC0415
    sys.path.insert(0, str(HERE.parent / "python"))
    from starbuddy_contract.contract import contract_from_doc, internal_contract  # noqa: PLC0415
    if doc is None:
        doc = load(INTERNAL_SPECS[1])
        c = internal_contract("1.0.0")
    else:
        c = contract_from_doc(doc, INTERNAL_SPECS[1], "urn:starbuddy:learning-internal:mutated")
    _check_internal_v1_structure(doc, errors)
    examples = doc["components"]["examples"]
    schema_of = {"GradeIngestKeypad": "GradeIngest", "GradeSummaryLevelC": "GradeSummary",
                 "DiagnoseIngestCanvasC": "DiagnoseIngest", "DiagnoseSummaryLevelC": "DiagnoseSummary",
                 "ModelRouteRequestZhixing": "ModelRouteRequest", "ModelRouteResponseZhixing": "ModelRouteResponse",
                 "PlanQueryToday": "PlanQuery", "PlanDigestToday": "PlanDigest",
                 "MasteryGetRequestOrchestrator": "ks:MasteryGetRequest", "MasteryGetResponseExecute": "ks:MasteryGetResponse",
                 "MasteryExplainRequestOrchestrator": "ks:MasteryExplainRequest",
                 "MasteryExplainResponseExecute": "ks:MasteryExplainResponse",
                 "VerificationRequestIKnow": "VerificationRequest", "VerificationTaskCreated": "VerificationTask",
                 "VerificationCancelRequestRollback": "VerificationCancelRequest",
                 "VerificationCancelResultCancelled": "VerificationCancelResult",
                 "ProfilePageEnsureSessionStart": "ProfilePageEnsureRequest", "ProfilePageEnsureCompiled": "ProfilePageEnsureResult",
                 "ProfilePageReadBasic": "ProfilePageReadRequest", "ProfilePageCompiled": "ProfilePage",
                 "StarMapNodesRequestBff": "StarMapNodesRequest", "StarMapNodesOk": "StarMapNodes", "StarMapNodesNoMap": "StarMapNodes"}
    for name, schema in schema_of.items():
        if name not in examples:
            errors.append(f"learning-internal.v1 缺少示例 {name}")
            continue
        errs = _validate_internal(c, schema, examples[name]["value"])
        if errs:
            errors.append(f"learning-internal.v1 示例 {name} 不符合 {schema}：{errs[:3]}")
    for label, schema, base, mutate in INTERNAL_V1_COUNTER:
        if base not in examples:
            continue  # 缺示例已在上面报错
        value = copy.deepcopy(examples[base]["value"])
        mutate(value)
        if not _validate_internal(c, schema, value):
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
        v11 = load(V11_SPEC)
        errors = (run_checks(doc, DEFAULT_SPEC) + [f"v1.0：{e}" for e in run_checks(v1, V1_SPEC)]
                  + [f"v1.1：{e}" for e in run_checks(v11, V11_SPEC)])
        try:
            n_breaking = check_breaking(doc, v1, errors)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"check_breaking 无法完成：{type(exc).__name__}: {exc}")
            n_breaking = 0
        additive: list[str] = []
        try:
            check_v11_additive(v1, v11, additive)
        except Exception as exc:  # noqa: BLE001
            additive.append(f"check_v11_additive 无法完成：{type(exc).__name__}: {exc}")
        errors += [f"v1.1：{e}" for e in additive]
    internal_refs = check_internal(errors) if len(sys.argv) <= 1 else None
    service_summary = None
    if len(sys.argv) <= 1:
        # 知识服务与洞察服务 OpenAPI v0.9（specs/service-openapi-v0.9/spec.md）
        import check_services  # noqa: PLC0415

        service_docs = check_services.load_all()
        errors.extend(check_services.run_service_checks(service_docs))
        service_summary = check_services.summary(service_docs)
        # 知识服务 v0.10 候选版（KGE-12 B：standard.search，只做增量）
        import check_knowledge_v010  # noqa: PLC0415

        v09_doc, v010_doc = check_knowledge_v010.load_docs()
        errors.extend(check_knowledge_v010.run_v010_checks(v09_doc, v010_doc))
        service_summary += "；" + check_knowledge_v010.summary(v010_doc)
    if errors:
        print(f"contract-test: 失败（{len(errors)} 项）")
        for e in errors:
            print(f"  - {e}")
        return 1
    n_ex = len(doc["components"]["examples"])
    sys.path.insert(0, str(HERE.parent / "tests"))
    from counterexamples import CASES  # noqa: PLC0415
    from counterexamples_v1 import V1_CASES  # noqa: PLC0415
    from counterexamples_v11 import V11_CASES  # noqa: PLC0415
    print(f"contract-test: 通过（OpenAPI 3.1 合法；{n_ex} 个示例符合 schema 且证据引用闭合；"
          f"{len(CASES)} 个反例全部被拒绝（v1.0 另加 {len(V1_CASES)} 个）；六方法信封、计量字段、无约束文本扫描、JobStatus 绑定、示例无 PII"
          + (f"；learning-internal v0 与 v1 合法、{internal_refs} 个跨文件引用可解析、v1 信封与摘要 {len(INTERNAL_V1_COUNTER)} 个反例全部被拒绝" if internal_refs is not None else "")
          + (f"；{service_summary}" if service_summary else "")
          + (f"；v1.0（已签字）合法、{len(v1['components']['examples'])} 个示例、{n_breaking} 条破坏性变更 v0.9 接受 / v1.0 拒绝"
             f"；v1.1 候选版合法、{len(v11['components']['examples'])} 个示例、只做增量（v1.0 的 schema / 路径 / 示例逐项不变），"
             f"present_item 另加 {len(V11_CASES)} 个反例全部被拒绝、答案类字段名黑名单与受限富文本探针通过"
             if len(sys.argv) <= 1 else "") + "）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
