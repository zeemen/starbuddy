"""审核台请求体导出（W13 T35，specs/review-console-types-t35 第三节第 1 条）。

从审核台后端的 OpenAPI（全部功能打开时注册的路由）取每个接口的请求体 schema，归一成
  { "方法 路径模板": { "字段": { "required": bool, "type": str, "nullable": bool, "enum": [...] | null } } }
写到 apps/web/src/lib/review-api.requests.json（提交进仓库）。前端测试 apps/web/tests/review-requests.test.ts 拿它与 review.ts 的
ReviewRequests 逐项比较；学习服务测试 tests/test_review_requests_export.py 核对提交的文件就是现在导出的结果。
格式核对（pattern、长度）不导出：前端不重复后端的格式校验。

  .venv/bin/python scripts/export_review_requests.py          写文件
  .venv/bin/python scripts/export_review_requests.py --check  只比较，不一致退出码 1
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
OUT = HERE.parents[1] / "apps" / "web" / "src" / "lib" / "review-api.requests.json"
NOTICE = "由 services/learning/scripts/export_review_requests.py 从审核台后端的 OpenAPI 导出，不要手改（W13 T35）"


def _field(schema: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    nullable = False
    if "anyOf" in schema:
        parts = [p for p in schema["anyOf"] if p.get("type") != "null"]
        nullable = len(parts) < len(schema["anyOf"])
        if len(parts) != 1:
            raise SystemExit(f"不支持的联合类型：{schema}")
        schema = parts[0]
    if "$ref" in schema:
        schema = defs[schema["$ref"].rsplit("/", 1)[-1]]
    typ = schema.get("type", "string")
    enum = schema.get("enum") or ([schema["const"]] if "const" in schema else None)
    out: dict[str, Any] = {"type": typ, "nullable": nullable, "enum": sorted(enum) if enum else None}
    if typ == "array":
        item = schema.get("items", {})
        out["items"] = {"type": item.get("type", "string"), "enum": sorted(item["enum"]) if item.get("enum") else None}
    return out


def export() -> dict[str, Any]:
    from review_console.app import create_app
    from review_console.config import FEATURES
    app = create_app(features=frozenset(FEATURES), internal_test=True, pages_settings=None)
    spec = app.openapi()
    defs = spec.get("components", {}).get("schemas", {})
    routes: dict[str, Any] = {}
    for path, ops in sorted(spec["paths"].items()):
        for method, op in sorted(ops.items()):
            body = op.get("requestBody")
            if not body:
                continue
            schema = body["content"]["application/json"]["schema"]
            model = defs[schema["$ref"].rsplit("/", 1)[-1]]
            if model.get("additionalProperties") is not False:
                raise SystemExit(f"{method.upper()} {path}：请求体模型不是严格模型（extra=forbid）")
            required = set(model.get("required", []))
            routes[f"{method.upper()} {path}"] = {
                name: {"required": name in required, **_field(prop, defs)} for name, prop in sorted(model["properties"].items())}
    return {"_notice": NOTICE, "routes": routes}


def render(doc: dict[str, Any]) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    text = render(export())
    if "--check" in argv:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print(f"review-requests: {OUT} 与后端模型不一致（重新运行 scripts/export_review_requests.py）", file=sys.stderr)
            return 1
        print("review-requests: 与后端模型一致")
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"review-requests: 写入 {OUT}（{len(json.loads(text)['routes'])} 个接口）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
