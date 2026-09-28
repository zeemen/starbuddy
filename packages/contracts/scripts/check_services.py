#!/usr/bin/env python3
"""知识服务与洞察服务 OpenAPI v0.9 的契约检查（specs/service-openapi-v0.9/spec.md 第 6 节）。

由 check_contract.py 在默认运行时一起调用；变异测试见 tests/test_mutations.py 的 SERVICE_MUTATIONS。
检查项：
1. OpenAPI 3.1 合法，跨文件 $ref 可解析；
2. KGE 第八章 10 个工具、SIE 第九章 9 个工具各有路径，operationId 与工具名对应；
3. 每个 200 响应基于统一信封 ServiceResultBase，信封外不允许额外字段；版本号三处一致；
4. 写入类工具的上下文要求 idempotency_key；公共层工具的上下文不带学生标识；两份契约都没有 usage_id；
5. 响应树没有无约束文本（允许清单之外）；公共层工具的请求与响应没有个人层字段名；
   洞察服务没有名次、百分位、概率类字段；面向学生家长的文字只有 RenderedStatement；
6. evidence.write 的证据等级只有 A、B；
7. 示例符合 schema、证据引用闭合、字段 ID 在字段字典里、无 PII；描述、枚举、示例零命中禁用推断词表；
8. 反例集（tests/service_counterexamples.py）全部被拒绝。
"""
from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate as validate_openapi
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

HERE = Path(__file__).resolve().parent
OPENAPI = HERE.parent / "openapi"
ROOT = HERE.parents[2]
FILES = {
    "plugin": OPENAPI / "plugin-api.v0.9.yaml",
    "knowledge": OPENAPI / "knowledge-service.v0.9.yaml",
    "insight": OPENAPI / "insight-service.v0.9.yaml",
}
FILE_TO_KEY = {p.name: k for k, p in FILES.items()}
FIELD_DICTIONARY = ROOT / "docs" / "design" / "sie" / "field-dictionary.v0.9.yaml"
LEXICON_DIR = ROOT / "docs" / "design" / "sie"

ENVELOPE_REQUIRED = {"result", "evidence_ids", "confidence", "source_version", "model_version",
                     "human_review_required", "contract_version"}
# 工具名 → (请求 schema, 响应 schema)
TOOLS = {
    "knowledge": {
        "node.resolve": ("NodeResolveRequest", "NodeResolveResponse"),
        "node.get": ("NodeGetRequest", "NodeGetResponse"),
        "node.path": ("NodePathRequest", "NodePathResponse"),
        "page.get": ("PageGetRequest", "PageGetResponse"),
        "item.search": ("ItemSearchRequest", "ItemSearchResponse"),
        "item.similar": ("ItemSimilarRequest", "ItemSimilarResponse"),
        "rubric.get": ("RubricGetRequest", "RubricGetResponse"),
        "evidence.write": ("EvidenceWriteRequest", "EvidenceWriteResponse"),
        "mastery.get": ("MasteryGetRequest", "MasteryGetResponse"),
        "mastery.explain": ("MasteryExplainRequest", "MasteryExplainResponse"),
    },
    "insight": {
        "insight.context": ("InsightContextRequest", "InsightContextResponse"),
        "insight.claims": ("InsightClaimsRequest", "InsightClaimsResponse"),
        "insight.explain": ("InsightExplainRequest", "InsightExplainResponse"),
        "insight.dispute": ("InsightDisputeRequest", "InsightDisputeResponse"),
        "insight.predict": ("InsightPredictRequest", "InsightPredictResponse"),
        "insight.reconcile": ("InsightReconcileRequest", "InsightReconcileResponse"),
        "insight.record": ("InsightRecordRequest", "InsightRecordResponse"),
        "insight.export": ("InsightExportRequest", "InsightExportResponse"),
        "insight.erase": ("InsightEraseRequest", "InsightEraseResponse"),
    },
}
WRITE_TOOLS = {
    "knowledge": {"evidence.write"},
    "insight": {"insight.dispute", "insight.predict", "insight.record", "insight.export", "insight.erase"},
}
PUBLIC_TOOLS = {"node.resolve", "node.get", "node.path", "page.get", "item.search", "item.similar", "rubric.get"}
# 响应里允许的自由文本：(owner, prop)。公共层是教研审核过的内容；洞察服务只允许模板渲染的陈述。
FREE_TEXT_ALLOW = {
    "knowledge": {
        ("NodeView", "name"), ("NodeView", "includes"), ("NodeView", "excludes"),
        ("TextbookProjection", "volume"), ("TextbookProjection", "chapter"), ("TextbookProjection", "quote_text"),
        ("RegionWeight", "typical_slots"), ("ScoringPointView", "keyword_family"),
        ("PageGetResponse.result", "body_md"),
    },
    "insight": {("RenderedStatement", "text")},
}
PERSONAL_NAMES = re.compile(r"(student|attempt|evidence_event|account|guardian|household|learner)", re.I)
RANKING_NAMES = re.compile(r"(rank|percentile|probabilit|position|排名|名次|概率)", re.I)
VISIBLE_STATUSES = {"hypothesis", "established", "to_verify"}
PII_PATTERNS = {
    "手机号": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "身份证号": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
    "邮箱": re.compile(r"[\w.+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"),
}


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_all() -> dict[str, dict]:
    return {k: load(p) for k, p in FILES.items()}


def _split_ref(ref: str, cur: str) -> tuple[str, str]:
    file, _, pointer = ref.partition("#")
    key = FILE_TO_KEY.get(Path(file).name, "?") if file else cur
    return key, pointer


def _resolve(docs: dict, ref: str, cur: str):
    key, pointer = _split_ref(ref, cur)
    node = docs.get(key, {})
    for part in pointer.strip("/").split("/"):
        if not isinstance(node, dict) or part not in node:
            return key, None, None
        node = node[part]
    return key, pointer.rsplit("/", 1)[-1], node


def walk(docs: dict, cur: str, root: str, visit_fn) -> None:
    """遍历 schema 子树（跨文件解析 $ref），对每个节点调用 visit_fn(node, owner, prop, trail, doc_key)。"""
    seen: set[int] = set()

    def visit(node, doc_key, owner, prop, trail):
        if not isinstance(node, dict) or id(node) in seen:
            return
        seen.add(id(node))
        if "$ref" in node:
            k, name, target = _resolve(docs, node["$ref"], doc_key)
            if target is not None:
                # 进入命名 schema：以它的名字为 owner（允许清单按“schema 名, 属性名”登记）
                visit(target, k, name, None, f"{trail}->{name}")
        visit_fn(node, owner, prop, trail, doc_key)
        for k in ("allOf", "anyOf", "oneOf"):
            for sub in node.get(k, []):
                visit(sub, doc_key, owner, prop, trail)
        for k in ("if", "then", "else", "not", "contains", "items", "additionalProperties"):
            if isinstance(node.get(k), dict):
                visit(node[k], doc_key, owner, prop, trail)
        for name, sub in node.get("properties", {}).items():
            visit(sub, doc_key, owner if prop is None else f"{owner}.{prop}", name, f"{trail}.{name}")

    visit(docs[cur]["components"]["schemas"][root], cur, root, None, root)


def free_text_leaks(docs: dict, svc: str, root: str) -> list[str]:
    allow = FREE_TEXT_ALLOW[svc]
    leaks: list[str] = []

    def fn(node, owner, prop, trail, _k):
        if "$ref" in node:
            return
        allowed = prop is not None and (owner, prop) in allow
        types = node.get("type")
        types = types if isinstance(types, list) else [types]
        if "string" in types and not allowed and not any(k in node for k in ("enum", "const", "pattern", "format")):
            leaks.append(f"{svc}:{trail}：无约束字符串（没有 enum / const / pattern / format）")
        if "string" in types and allowed and "maxLength" not in node:
            leaks.append(f"{svc}:{trail}：允许的自由文本也必须有 maxLength")
        pat = node.get("pattern")
        if isinstance(pat, str) and not (pat.startswith("^") and pat.endswith("$")):
            leaks.append(f"{svc}:{trail}：pattern 没有同时锚定开头和结尾：{pat}")
        if node.get("additionalProperties") is True:
            leaks.append(f"{svc}:{trail}：开放对象（additionalProperties: true）")
        if node.get("patternProperties"):
            leaks.append(f"{svc}:{trail}：patternProperties 可以绕过字段白名单")
        # ServiceResultBase 是信封基类，由各响应的 unevaluatedProperties: false 收口
        if node.get("type") == "object" and "properties" in node and node.get("additionalProperties") is not False \
                and "unevaluatedProperties" not in node and not (owner == "ServiceResultBase" and prop is None):
            leaks.append(f"{svc}:{trail}：对象没有 additionalProperties: false")

    walk(docs, svc, root, fn)
    return leaks


def names_in(docs: dict, svc: str, root: str, with_values: bool = True) -> list[tuple[str, str]]:
    """子树里出现的属性名（以及字符串枚举值）：[(名字, 路径)]。"""
    out: list[tuple[str, str]] = []

    def fn(node, _owner, _prop, trail, _k):
        for name in node.get("properties", {}):
            out.append((name, f"{trail}.{name}"))
        if not with_values:
            return
        for v in node.get("enum", []) + ([node["const"]] if "const" in node else []):
            if isinstance(v, str):
                out.append((v, f"{trail}=“{v}”"))

    walk(docs, svc, root, fn)
    return out


def _requires(docs: dict, svc: str, schema_ref: str, field: str) -> bool:
    _, _, node = _resolve(docs, schema_ref, svc)
    if not isinstance(node, dict):
        return False
    if field in node.get("required", []):
        return True
    return any(_requires(docs, svc, sub["$ref"], field) if "$ref" in sub else field in sub.get("required", [])
               for sub in node.get("allOf", []))


def check_structure(docs: dict, errors: list[str]) -> None:
    for svc, tools in TOOLS.items():
        doc = docs[svc]
        schemas = doc["components"]["schemas"]
        base = schemas.get("ServiceResultBase", {})
        missing = ENVELOPE_REQUIRED - set(base.get("required", []))
        if missing:
            errors.append(f"{svc}：ServiceResultBase 缺少必填字段 {sorted(missing)}")
        version = doc.get("info", {}).get("version")
        if base.get("properties", {}).get("contract_version", {}).get("const") != version:
            errors.append(f"{svc}：info.version（{version}）与信封 contract_version 不一致")
        op_ids = {op.get("operationId") for item in doc.get("paths", {}).values() for op in item.values()
                  if isinstance(op, dict)}
        extra = op_ids - {t.replace(".", "_") for t in tools}
        if extra:
            errors.append(f"{svc}：有不在 KGE / SIE 工具清单里的操作 {sorted(extra)}")
        for tool, (req_name, resp_name) in tools.items():
            path = f"/v0/{tool}"
            op = doc.get("paths", {}).get(path, {}).get("post")
            if not op:
                errors.append(f"{svc}：缺少工具路径 {path}")
                continue
            if op.get("operationId") != tool.replace(".", "_"):
                errors.append(f"{svc}：{path} 的 operationId 应为 {tool.replace('.', '_')}")
            try:
                req = op["requestBody"]["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]
                resp = op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]
            except (KeyError, TypeError):
                errors.append(f"{svc}：{path} 缺少请求体或 200 响应")
                continue
            if (req, resp) != (req_name, resp_name):
                errors.append(f"{svc}：{path} 的请求 / 响应应为 {req_name} / {resp_name}")
                continue
            rs = schemas.get(resp_name, {})
            if "#/components/schemas/ServiceResultBase" not in [s.get("$ref") for s in rs.get("allOf", [])]:
                errors.append(f"{svc}：{resp_name} 没有基于统一信封 ServiceResultBase")
            if rs.get("unevaluatedProperties") is not False:
                errors.append(f"{svc}：{resp_name} 必须设 unevaluatedProperties: false")
            ctx_ref = schemas.get(req_name, {}).get("properties", {}).get("context", {}).get("$ref")
            if not ctx_ref:
                errors.append(f"{svc}：{req_name} 没有 context")
                continue
            if tool in WRITE_TOOLS[svc] and not _requires(docs, svc, ctx_ref, "idempotency_key"):
                errors.append(f"{svc}：写入类工具 {tool} 的上下文必须要求 idempotency_key")
            if tool in PUBLIC_TOOLS:
                for name, where in names_in(docs, svc, req_name, False) + names_in(docs, svc, resp_name, False):
                    if PERSONAL_NAMES.search(name):
                        errors.append(f"{svc}：公共层工具 {tool} 出现个人层字段 {where}（规则 13）")
            errors.extend(free_text_leaks(docs, svc, resp_name))
            if svc == "insight":
                for name, where in names_in(docs, svc, req_name) + names_in(docs, svc, resp_name):
                    if RANKING_NAMES.search(name):
                        errors.append(f"insight：{tool} 出现名次、百分位或概率类字段 {where}（平台规划 v1.3 §3）")
        raw = yaml.safe_dump(doc.get("paths", {})) + yaml.safe_dump(schemas)
        if re.search(r"\busage_id\b", raw):
            errors.append(f"{svc}：出现 usage_id。本接口不计次，计量在插件请求与 BFF 层（规则 4）")

    ins = docs["insight"]["components"]["schemas"]
    rs = ins.get("RenderedStatement", {})
    for f in ("claim_id", "text", "status"):
        if f not in rs.get("required", []):
            errors.append(f"insight：RenderedStatement.{f} 必须必填（规则 11）")
    if (rs.get("properties", {}).get("text", {}).get("maxLength") or 10**9) > 300:
        errors.append("insight：RenderedStatement.text 必须限长 300")
    if set(ins.get("ClaimStatusVisible", {}).get("enum", [])) != VISIBLE_STATUSES:
        errors.append("insight：可展示的判断状态只能是 hypothesis / established / to_verify")
    levels = set(docs["knowledge"]["components"]["schemas"].get("EvidenceLevel", {}).get("enum", []))
    if not levels or not levels <= {"A", "B"}:
        errors.append(f"knowledge：evidence.write 的证据等级只能是 A、B（C 级不进学习者模型），实际 {sorted(levels)}")


def _registry(docs: dict) -> Registry:
    def rewrite(node, cur):
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if k == "$ref" and isinstance(v, str):
                    key, pointer = _split_ref(v, cur)
                    out[k] = f"urn:{key}#{pointer}"
                else:
                    out[k] = rewrite(v, cur)
            return out
        if isinstance(node, list):
            return [rewrite(v, cur) for v in node]
        return node

    reg = Registry()
    for key, doc in docs.items():
        reg = reg.with_resource(f"urn:{key}", Resource(contents=rewrite(doc, key), specification=DRAFT202012))
    return reg


def validator(docs: dict, svc: str, schema: str, registry: Registry | None = None) -> Draft202012Validator:
    return Draft202012Validator({"$ref": f"urn:{svc}#/components/schemas/{schema}"},
                                registry=registry or _registry(docs), format_checker=FormatChecker())


def _collect(node, keys: set[str], acc: set[str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k in keys and isinstance(v, str):
                acc.add(v)
            elif k in keys and isinstance(v, list):
                acc.update(x for x in v if isinstance(x, str))
            else:
                _collect(v, keys, acc)
    elif isinstance(node, list):
        for v in node:
            _collect(v, keys, acc)


def _dictionary_ids() -> set[str]:
    data = load(FIELD_DICTIONARY)
    return {f["id"] for f in data.get("fields", [])}


def check_examples(docs: dict, errors: list[str]) -> None:
    registry = _registry(docs)
    field_ids = _dictionary_ids()
    for svc, tools in TOOLS.items():
        doc = docs[svc]
        examples = doc.get("components", {}).get("examples", {})
        used: set[str] = set()
        for tool, (req_name, resp_name) in tools.items():
            op = doc.get("paths", {}).get(f"/v0/{tool}", {}).get("post", {})
            pairs = [(op.get("requestBody", {}).get("content", {}).get("application/json", {}), req_name, "请求"),
                     (op.get("responses", {}).get("200", {}).get("content", {}).get("application/json", {}), resp_name, "响应")]
            for media, schema, label in pairs:
                exs = (media or {}).get("examples", {})
                if not exs:
                    errors.append(f"{svc}：{tool} 没有{label}示例")
                for ex in exs.values():
                    name = ex.get("$ref", "").rsplit("/", 1)[-1]
                    used.add(name)
                    value = examples.get(name, {}).get("value")
                    for e in validator(docs, svc, schema, registry).iter_errors(value):
                        errors.append(f"{svc}：示例 {name} 不符合 {schema}：{'/'.join(map(str, e.absolute_path))}：{e.message[:160]}")
                    if label == "响应" and isinstance(value, dict):
                        refs: set[str] = set()
                        _collect(value.get("result"), {"evidence_ids", "evidence_id"}, refs)
                        envelope = set(value.get("evidence_ids", []))
                        if not refs <= envelope:
                            errors.append(f"{svc}：示例 {name} 的 result 引用了信封 evidence_ids 之外的证据 {sorted(refs - envelope)}")
                    ids: set[str] = set()
                    _collect(value, {"field_id"}, ids)
                    for fid in sorted(ids - field_ids):
                        errors.append(f"{svc}：示例 {name} 的字段 {fid} 不在字段字典里")
        unused = set(examples) - used
        if unused:
            errors.append(f"{svc}：有示例没有挂到任何工具上 {sorted(unused)}")
        raw = yaml.safe_dump(examples, allow_unicode=True)
        for label, pat in PII_PATTERNS.items():
            hit = pat.search(raw)
            if hit:
                errors.append(f"{svc}：示例里出现疑似{label}：{hit.group(0)}")


def _texts(node, acc: list[str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("description", "summary", "title") and isinstance(v, str):
                acc.append(v)
            elif k in ("enum",) and isinstance(v, list):
                acc.extend(x for x in v if isinstance(x, str))
            elif k == "value" and not isinstance(v, (dict, list)):
                if isinstance(v, str):
                    acc.append(v)
            else:
                _texts(v, acc)
    elif isinstance(node, list):
        for v in node:
            _texts(v, acc)
    elif isinstance(node, str):
        pass


def _example_strings(node, acc: list[str]) -> None:
    if isinstance(node, dict):
        for v in node.values():
            _example_strings(v, acc)
    elif isinstance(node, list):
        for v in node:
            _example_strings(v, acc)
    elif isinstance(node, str):
        acc.append(node)


def check_lexicon(docs: dict, errors: list[str]) -> None:
    sys.path.insert(0, str(LEXICON_DIR))
    import sie_lexicon  # noqa: PLC0415

    lex = sie_lexicon.load()
    for svc in TOOLS:
        texts: list[str] = []
        _texts({k: v for k, v in docs[svc].items() if k != "components"}, texts)
        _texts(docs[svc]["components"].get("schemas", {}), texts)
        _example_strings(docs[svc]["components"].get("examples", {}), texts)
        for t in texts:
            for h in lex.find(t):
                errors.append(f"{svc}：描述、枚举或示例命中禁用推断词表 {h.category}（{h.match}）：{t[:60]}")


def check_counterexamples(docs: dict, errors: list[str]) -> None:
    sys.path.insert(0, str(HERE.parent / "tests"))
    from service_counterexamples import CASES  # noqa: PLC0415

    registry = _registry(docs)
    for case in CASES:
        base = docs[case.service]["components"]["examples"][case.base]["value"]
        value = copy.deepcopy(base)
        out = case.mutate(value)
        value = value if out is None else out
        if validator(docs, case.service, case.schema, registry).is_valid(value):
            errors.append(f"服务契约反例未被拒绝：{case.name}（{case.rule}）")


def run_service_checks(docs: dict | None = None) -> list[str]:
    docs = docs or load_all()
    errors: list[str] = []
    for svc in TOOLS:
        try:
            validate_openapi(copy.deepcopy(docs[svc]), base_uri=FILES[svc].as_uri())
        except Exception as exc:  # noqa: BLE001 - 打印校验器的原始信息
            errors.append(f"{svc}：OpenAPI 3.1 校验失败：{str(exc)[:400]}")
    for fn in (check_structure, check_examples, check_lexicon, check_counterexamples):
        try:
            fn(docs, errors)
        except Exception as exc:  # noqa: BLE001 - 结构不完整时报错而不是崩溃
            errors.append(f"{fn.__name__} 无法完成：{type(exc).__name__}: {exc}")
    return errors


def summary(docs: dict) -> str:
    sys.path.insert(0, str(HERE.parent / "tests"))
    from service_counterexamples import CASES  # noqa: PLC0415

    n_ex = sum(len(docs[s]["components"]["examples"]) for s in TOOLS)
    return (f"知识服务 {len(TOOLS['knowledge'])} 个工具、洞察服务 {len(TOOLS['insight'])} 个工具合法；"
            f"{n_ex} 个示例符合 schema；{len(CASES)} 个服务反例全部被拒绝")


if __name__ == "__main__":
    errs = run_service_checks()
    for e in errs:
        print(f"  - {e}")
    print("service-contract:", "失败" if errs else "通过（" + summary(load_all()) + "）")
    sys.exit(1 if errs else 0)
