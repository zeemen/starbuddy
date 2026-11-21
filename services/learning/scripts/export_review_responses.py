"""审核台响应结构导出（specs/review-console-responses 第二节第 3 条）。

从 review_console/responses.py 的 ROUTES（每个接口的响应模型）生成 JSON schema（校验模式：带默认值的字段是“可以不给”，与路由的
response_model_exclude_unset 一致；序列化模式会把它们标成必填，不用），归一成递归结构：
  节点 = { "type": string | integer | number | boolean | array | object | map | any | tagged, "nullable": bool, "enum": [...] | null,
          "items": 节点（array）, "fields": { 名: { "required": bool, ...节点 } }（object）, "values": 节点（map），
          "tag": 字段名, "cases": { 取值: 节点 | null }（tagged：随同一对象里另一字段的取值分支，见 _tagged） }
写到 apps/web/src/lib/review-api.responses.json（提交进仓库）。前端测试 apps/web/tests/review-responses.test.ts 拿它与 review.ts 的
ReviewResponses 递归比较；学习服务测试 tests/test_review_responses_export.py 核对提交的文件就是现在导出的结果。
导出前核对：应用（全部功能打开）注册的接口集合与 ROUTES 相同，每个模型都是 extra="forbid"。

  .venv/bin/python scripts/export_review_responses.py          写文件
  .venv/bin/python scripts/export_review_responses.py --check  只比较，不一致退出码 1
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
OUT = HERE.parents[1] / "apps" / "web" / "src" / "lib" / "review-api.responses.json"
NOTICE = "由 services/learning/scripts/export_review_responses.py 从审核台响应模型导出，不要手改（specs/review-console-responses）"
SCALARS = {"string", "integer", "number", "boolean"}


def _node(schema: dict[str, Any], defs: dict[str, Any], where: str) -> dict[str, Any]:
    nullable = False
    if "anyOf" in schema:
        parts = [p for p in schema["anyOf"] if p.get("type") != "null"]
        nullable = len(parts) < len(schema["anyOf"])
        if len(parts) != 1:
            raise SystemExit(f"{where}：不支持的联合类型 {schema}")
        schema = parts[0]
    if "$ref" in schema:
        schema = defs[schema["$ref"].rsplit("/", 1)[-1]]
    if not set(schema) - {"title", "default", "description"}:  # Any（只有说明性的键）
        return {"type": "any", "nullable": True, "enum": None}
    enum = schema.get("enum") or ([schema["const"]] if "const" in schema else None)
    typ = schema.get("type")
    if typ == "null":
        raise SystemExit(f"{where}：只能为空的字段")
    if typ in SCALARS:
        return {"type": typ, "nullable": nullable, "enum": sorted(enum) if enum else None}
    if enum:  # 没写 type 的 Literal
        return {"type": "string", "nullable": nullable, "enum": sorted(enum)}
    if typ == "array":
        return {"type": "array", "nullable": nullable, "enum": None, "items": _node(schema.get("items", {}), defs, f"{where}[]")}
    if typ == "object" and "properties" in schema:
        if schema.get("additionalProperties") is not False:
            raise SystemExit(f"{where}：响应模型不是严格模型（extra=forbid）")
        required = set(schema.get("required", []))
        return {"type": "object", "nullable": nullable, "enum": None, "fields": {
            name: {"required": name in required, **_node(prop, defs, f"{where}.{name}")} for name, prop in sorted(schema["properties"].items())}}
    if typ == "object":
        extra = schema.get("additionalProperties", True)
        if extra is True or not set(extra) - {"title", "default", "description"}:  # dict[str, Any]：自由结构
            return {"type": "any", "nullable": nullable, "enum": None}
        return {"type": "map", "nullable": nullable, "enum": None, "values": _node(extra, defs, f"{where}{{}}")}
    raise SystemExit(f"{where}：认不出的 schema {schema}")


def _tagged(node: dict[str, Any], field: str, tag: str, cases: dict[str, Any], where: str) -> dict[str, Any]:
    """按另一字段（tag）分支的字段（复核任务的 source 按 source_kind，specs/review-task-source-models）：
    { "type": "tagged", "tag": 字段名, "cases": { 取值: 节点 | null（这一支必须为空） } }；分支集合须与 tag 字段的枚举相同。"""
    tag_enum = node["fields"][tag]["enum"]
    if sorted(cases) != tag_enum:
        raise SystemExit(f"{where}：分支 {sorted(cases)} 与 {tag} 的枚举 {tag_enum} 不同")
    out: dict[str, Any] = {}
    for value, model in sorted(cases.items()):
        if model is None:
            out[value] = None
            continue
        schema = model.model_json_schema(mode="validation")
        out[value] = _node(schema, schema.get("$defs", {}), f"{where}[{tag}={value}]")
    f = node["fields"][field]
    return {"required": f["required"], "type": "tagged", "nullable": f["nullable"], "enum": None, "tag": tag, "cases": out}


def export() -> dict[str, Any]:
    from review_console.app import create_app
    from review_console.config import FEATURES
    from review_console.responses import ROUTES
    app = create_app(features=frozenset(FEATURES), internal_test=True, pages_settings=None)
    registered = {f"{m.upper()} {p}" for p, ops in app.openapi()["paths"].items() for m in ops if p.startswith("/review/v0/")}
    if registered != set(ROUTES):
        raise SystemExit(f"应用注册的接口与 responses.ROUTES 不同：多 {sorted(registered - set(ROUTES))}，少 {sorted(set(ROUTES) - registered)}")
    routes: dict[str, Any] = {}
    for route, model in sorted(ROUTES.items()):
        schema = model.model_json_schema(mode="validation")
        node = _node(schema, schema.get("$defs", {}), route)
        for field, (tag, cases) in getattr(model, "TAGGED", {}).items():
            node["fields"][field] = _tagged(node, field, tag, cases, f"{route}.{field}")
        routes[route] = node
    return {"_notice": NOTICE, "routes": routes}


def render(doc: dict[str, Any]) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    text = render(export())
    if "--check" in argv:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print(f"review-responses: {OUT} 与后端响应模型不一致（重新运行 scripts/export_review_responses.py）", file=sys.stderr)
            return 1
        print("review-responses: 与后端响应模型一致")
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"review-responses: 写入 {OUT}（{len(json.loads(text)['routes'])} 个接口）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
